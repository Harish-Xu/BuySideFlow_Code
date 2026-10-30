#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.linalg import solve

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe, shared_connection_session


SCRIPT_DIR = Path(__file__).resolve().parent
SQL_PATH = SCRIPT_DIR / "refer.sql"
OUTPUT_PATH = SCRIPT_DIR / "result.csv"

ETF_POOL = [
    "518880",
    "159985",
    "513100",
    "510300",
    "159915",
    "159992",
    "515700",
    "510150",
    "515790",
    "515880",
    "512720",
    "512660",
    "159740",
]
MOMENTUM_WINDOW = 34
LOOKBACK_RETURNS = 250
PRICE_WINDOW = LOOKBACK_RETURNS + 1
TOP_N = 3
MOMENTUM_ANNUALIZATION_DAYS = 250
VOLATILITY_ANNUALIZATION_DAYS = 252
LAMBDA_RISK_AVERSION = 10.0
SHRINKAGE_W = 0.2
MAX_PRICE_MISSING_RATIO = 0.10

SQL_BLOCK_RE = re.compile(
    r"^-- name:\s*(?P<name>[a-zA-Z0-9_]+)\s*$\n(?P<sql>.*?)(?=^-- name:|\Z)",
    re.M | re.S,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build ETF momentum + anchored EPO snapshot.")
    parser.add_argument("--as-of-date", default="2025-12-31")
    parser.add_argument("--output-path", default=str(OUTPUT_PATH))
    return parser.parse_args()


def load_sql_blocks() -> dict[str, str]:
    sql_text = SQL_PATH.read_text(encoding="utf-8")
    blocks = {
        match.group("name"): match.group("sql").strip()
        for match in SQL_BLOCK_RE.finditer(sql_text)
    }
    if not blocks:
        raise ValueError("No SQL blocks found in refer.sql")
    return blocks


SQL_BLOCKS = load_sql_blocks()


def etf_pool_rows() -> str:
    return "\n    UNION ALL\n    ".join(
        f"SELECT '{code}' AS etf_code" for code in ETF_POOL
    )


def render_sql(name: str, **params: object) -> str:
    if name not in SQL_BLOCKS:
        raise KeyError(f"Missing SQL block: {name}")
    return SQL_BLOCKS[name].format(**{key: str(value) for key, value in params.items()})


def query_block(name: str, timeout: int = 600, **params: object) -> pd.DataFrame:
    df = query_to_dataframe(render_sql(name, **params), max_rows=0, timeout=timeout)
    if df is None:
        raise RuntimeError(f"SQL returned None: {name}")
    return df


def fetch_price_panel(as_of_date: str) -> pd.DataFrame:
    panel = query_block(
        "price_panel",
        timeout=600,
        as_of_date=pd.Timestamp(as_of_date).strftime("%Y-%m-%d"),
        etf_pool_rows=etf_pool_rows(),
        etf_count=len(ETF_POOL),
        price_window=PRICE_WINDOW,
    )
    if panel.empty:
        raise ValueError(f"No ETF price panel for as_of_date={as_of_date}")

    panel["signal_date"] = pd.to_datetime(panel["signal_date"])
    panel["rebalance_date"] = pd.to_datetime(panel["rebalance_date"])
    panel["trading_day"] = pd.to_datetime(panel["trading_day"])
    panel["close_price"] = pd.to_numeric(panel["close_price"], errors="coerce")

    found_codes = set(panel["etf_code"].dropna().astype(str).unique())
    missing_codes = [code for code in ETF_POOL if code not in found_codes]
    if missing_codes:
        raise ValueError(f"ETF code(s) not found in fund universe: {missing_codes}")

    signal_dates = panel["signal_date"].dropna().unique()
    rebalance_dates = panel["rebalance_date"].dropna().unique()
    if len(signal_dates) != 1:
        raise ValueError(f"Expected one signal date, got {len(signal_dates)}")
    if len(rebalance_dates) != 1:
        raise ValueError(f"Expected one rebalance date, got {len(rebalance_dates)}")
    return panel


def calculate_momentum_score(price_series: pd.Series) -> float:
    if len(price_series) != MOMENTUM_WINDOW:
        raise ValueError(f"Momentum window must be {MOMENTUM_WINDOW}, got {len(price_series)}")
    if price_series.isna().any() or (price_series <= 0).any():
        raise ValueError("Momentum price window contains missing or non-positive close")

    log_price = np.log(price_series.to_numpy(dtype=float))
    x = np.arange(len(log_price), dtype=float)
    slope, intercept = np.polyfit(x, log_price, 1)
    fitted = slope * x + intercept
    ss_total = np.sum((log_price - log_price.mean()) ** 2)
    if ss_total <= 0:
        return 0.0
    r_squared = 1.0 - np.sum((log_price - fitted) ** 2) / ss_total
    annualized_slope_return = np.exp(slope * MOMENTUM_ANNUALIZATION_DAYS) - 1.0
    return float(annualized_slope_return * r_squared)


def anchored_epo_weights(returns: pd.DataFrame, signal: np.ndarray, anchor: np.ndarray) -> np.ndarray:
    cov = returns.cov(ddof=0).to_numpy(dtype=float)
    corr = returns.corr().to_numpy(dtype=float)
    if not np.isfinite(cov).all() or not np.isfinite(corr).all():
        raise ValueError("Covariance or correlation matrix contains invalid values")

    diagonal_var = np.diag(cov)
    if np.any(diagonal_var <= 0):
        raise ValueError("Return variances must be positive")

    n_assets = len(diagonal_var)
    identity = np.eye(n_assets)
    variance_diag = np.diag(diagonal_var)
    std_matrix = np.diag(np.sqrt(diagonal_var))

    shrunk_corr = (1.0 - SHRINKAGE_W) * corr + SHRINKAGE_W * identity
    cov_tilde = std_matrix @ shrunk_corr @ std_matrix
    inv_shrunk_cov = solve(cov_tilde, identity, assume_a="pos")

    signal_adjusted = signal / LAMBDA_RISK_AVERSION
    denominator = np.sqrt(
        signal_adjusted.T @ inv_shrunk_cov @ cov_tilde @ inv_shrunk_cov @ signal_adjusted
    )
    if denominator <= 0:
        raise ValueError("Anchored EPO denominator is non-positive")
    gamma = np.sqrt(anchor.T @ cov_tilde @ anchor) / denominator

    raw_weights = inv_shrunk_cov @ (
        ((1.0 - SHRINKAGE_W) * gamma * signal_adjusted)
        + (SHRINKAGE_W * variance_diag @ anchor)
    )

    long_only = np.clip(raw_weights, a_min=0.0, a_max=None)
    total = long_only.sum()
    if total <= 0:
        raise ValueError("All EPO weights are non-positive after long-only constraint")
    return long_only / total


def build_single_result(price_panel: pd.DataFrame) -> pd.DataFrame:
    signal_date = pd.Timestamp(price_panel["signal_date"].dropna().iloc[0]).strftime("%Y-%m-%d")
    rebalance_date = pd.Timestamp(price_panel["rebalance_date"].dropna().iloc[0]).strftime("%Y-%m-%d")

    info = (
        price_panel[["etf_code", "etf_name"]]
        .drop_duplicates(subset=["etf_code"])
        .set_index("etf_code")
    )
    price_matrix = (
        price_panel.pivot(index="trading_day", columns="etf_code", values="close_price")
        .sort_index()
        .reindex(columns=ETF_POOL)
    )
    if len(price_matrix) != PRICE_WINDOW:
        raise ValueError(f"Price matrix must have {PRICE_WINDOW} trading days, got {len(price_matrix)}")
    if price_matrix.iloc[-1].isna().any():
        missing = price_matrix.columns[price_matrix.iloc[-1].isna()].tolist()
        raise ValueError(f"Signal day close price missing for ETF(s): {missing}")

    momentum_rows: list[dict[str, object]] = []
    for etf_code in ETF_POOL:
        price_series = price_matrix[etf_code]
        momentum_window = price_series.tail(MOMENTUM_WINDOW)
        if momentum_window.isna().any():
            continue
        missing_ratio = float(price_series.isna().mean())
        if missing_ratio > MAX_PRICE_MISSING_RATIO:
            continue
        if pd.isna(price_series.iloc[0]):
            continue
        score = calculate_momentum_score(momentum_window)
        if score <= 0:
            continue
        momentum_rows.append(
            {
                "ETF代码": etf_code,
                "动量得分": score,
                "缺失比例": missing_ratio,
            }
        )

    momentum = pd.DataFrame(momentum_rows)
    if momentum.empty:
        raise ValueError("No ETF passed positive momentum filter")
    selected = momentum.sort_values(
        ["动量得分", "ETF代码"],
        ascending=[False, True],
        kind="mergesort",
    ).head(TOP_N).copy()
    if len(selected) < TOP_N:
        raise ValueError(f"Only {len(selected)} ETF(s) passed filters, expected {TOP_N}")

    selected_codes = selected["ETF代码"].tolist()
    selected_prices = price_matrix[selected_codes].ffill()
    if selected_prices.isna().any().any():
        raise ValueError("Selected ETF price window still has missing values after forward fill")

    returns = selected_prices.pct_change().dropna(how="any")
    if len(returns) != LOOKBACK_RETURNS:
        raise ValueError(f"Return matrix must have {LOOKBACK_RETURNS} rows, got {len(returns)}")

    signal = returns.mean().to_numpy(dtype=float)
    variance = returns.var(ddof=0).to_numpy(dtype=float)
    anchor = 1.0 / variance
    anchor = anchor / anchor.sum()
    epo = anchored_epo_weights(returns, signal=signal, anchor=anchor)
    volatility = returns.std(ddof=0) * np.sqrt(VOLATILITY_ANNUALIZATION_DAYS)

    selected["信号日"] = signal_date
    selected["调仓日"] = rebalance_date
    selected["ETF名称"] = selected["ETF代码"].map(info["etf_name"].to_dict())
    selected["250日年化波动率"] = selected["ETF代码"].map(volatility.to_dict())
    selected["锚权重"] = anchor
    selected["EPO权重"] = epo

    result = selected[
        [
            "信号日",
            "调仓日",
            "ETF代码",
            "ETF名称",
            "动量得分",
            "250日年化波动率",
            "锚权重",
            "EPO权重",
        ]
    ].copy()

    numeric_cols = ["动量得分", "250日年化波动率", "锚权重", "EPO权重"]
    for col in numeric_cols:
        result[col] = result[col].astype(float).round(8)

    if not np.isclose(result["EPO权重"].sum(), 1.0, atol=1e-6):
        raise ValueError("EPO weights do not sum to 1")
    return result


def main() -> pd.DataFrame:
    args = parse_args()
    as_of_date = pd.Timestamp(args.as_of_date).strftime("%Y-%m-%d")
    with shared_connection_session():
        price_panel = fetch_price_panel(as_of_date)
    result = build_single_result(price_panel)

    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
