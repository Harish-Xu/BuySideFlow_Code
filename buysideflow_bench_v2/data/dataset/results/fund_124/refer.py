#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


SCRIPT_DIR = Path(__file__).resolve().parent
SQL_PATH = SCRIPT_DIR / "refer.sql"
OUTPUT_PATH = SCRIPT_DIR / "result.csv"

AS_OF_DATE = "2025-12-31"
ETF_POOL = [
    "512660",
    "511010",
    "510880",
    "159915",
    "513050",
    "510050",
    "588100",
    "512100",
    "518800",
    "513060",
    "512010",
    "513100",
    "512720",
    "515880",
    "159920",
    "159922",
    "513520",
    "515000",
    "515790",
    "515700",
    "512400",
    "512480",
    "510230",
    "159928",
]
LOOKBACK_RETURNS = 729
PRICE_WINDOW = LOOKBACK_RETURNS + 1
MOMENTUM_PRICE_WINDOW = 25
ANNUALIZATION_DAYS = 252
MOMENTUM_ANNUALIZATION_DAYS = 250
LOW_VOL_THRESHOLD = 0.05
HIGH_VOL_THRESHOLD = 0.33
LOW_CORR_COUNT = 4
MOMENTUM_MIN = -0.5
MOMENTUM_MAX = 4.5

SQL_BLOCK_RE = re.compile(
    r"^-- name:\s*(?P<name>[a-zA-Z0-9_]+)\s*$\n(?P<sql>.*?)(?=^-- name:|\Z)",
    re.M | re.S,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build fund_124 ETF single-period signal.")
    parser.add_argument("--as-of-date", default=AS_OF_DATE)
    parser.add_argument("--output-path", default=str(OUTPUT_PATH))
    return parser.parse_args()


def load_sql_blocks() -> dict[str, str]:
    sql_text = SQL_PATH.read_text(encoding="utf-8")
    return {
        match.group("name"): match.group("sql").strip()
        for match in SQL_BLOCK_RE.finditer(sql_text)
    }


SQL_BLOCKS = load_sql_blocks()


def etf_pool_rows() -> str:
    return "\n    UNION ALL\n    ".join(
        f"SELECT '{code}' AS etf_code" for code in ETF_POOL
    )


def render_sql(name: str, **params: object) -> str:
    if name not in SQL_BLOCKS:
        raise KeyError(f"Missing SQL block: {name}")
    return SQL_BLOCKS[name].format(**{key: str(value) for key, value in params.items()})


def fetch_price_panel(as_of_date: str) -> pd.DataFrame:
    sql = render_sql(
        "price_panel",
        as_of_date=pd.Timestamp(as_of_date).strftime("%Y-%m-%d"),
        etf_pool_rows=etf_pool_rows(),
        etf_count=len(ETF_POOL),
        price_window=PRICE_WINDOW,
    )
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        raise ValueError("未取得 ETF 收盘价面板。")

    df = df.copy()
    for col in ["signal_date", "rebalance_date", "trading_day"]:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    df["close_price"] = pd.to_numeric(df["close_price"], errors="coerce")

    found_codes = set(df["etf_code"].dropna().astype(str).unique())
    missing_codes = [code for code in ETF_POOL if code not in found_codes]
    if missing_codes:
        raise ValueError(f"ETF 池代码未匹配到证券主表或行情: {missing_codes}")

    signal_dates = df["signal_date"].dropna().unique()
    rebalance_dates = df["rebalance_date"].dropna().unique()
    if len(signal_dates) != 1:
        raise ValueError(f"Expected one signal date, got {len(signal_dates)}")
    if len(rebalance_dates) != 1:
        raise ValueError(f"Expected one rebalance date, got {len(rebalance_dates)}")
    return df


def calc_momentum_score(price_series: pd.Series) -> float:
    if len(price_series) != MOMENTUM_PRICE_WINDOW:
        raise ValueError(f"Momentum window must have {MOMENTUM_PRICE_WINDOW} prices")
    if price_series.isna().any() or (price_series <= 0).any():
        return np.nan

    log_price = np.log(price_series.to_numpy(dtype=float))
    x = np.arange(len(log_price), dtype=float)
    slope, intercept = np.polyfit(x, log_price, 1)
    fitted = slope * x + intercept
    total_ss = float(np.sum((log_price - log_price.mean()) ** 2))
    if total_ss <= 0:
        return 0.0
    r_squared = 1.0 - float(np.sum((log_price - fitted) ** 2)) / total_ss
    annualized_return = np.exp(slope * MOMENTUM_ANNUALIZATION_DAYS) - 1.0
    return float(annualized_return * r_squared)


def build_signal(price_panel: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    signal_date = pd.Timestamp(price_panel["signal_date"].dropna().iloc[0]).strftime("%Y-%m-%d")
    rebalance_date = pd.Timestamp(price_panel["rebalance_date"].dropna().iloc[0]).strftime("%Y-%m-%d")
    info = (
        price_panel[["etf_code", "etf_name"]]
        .drop_duplicates(subset=["etf_code"])
        .set_index("etf_code")["etf_name"]
        .to_dict()
    )
    price_matrix = (
        price_panel.pivot(index="trading_day", columns="etf_code", values="close_price")
        .sort_index()
        .reindex(columns=ETF_POOL)
    )
    if len(price_matrix) != PRICE_WINDOW:
        raise ValueError(f"Price matrix must have {PRICE_WINDOW} trading days, got {len(price_matrix)}")
    if price_matrix.isna().any().any():
        missing = price_matrix.columns[price_matrix.isna().any()].tolist()
        raise ValueError(f"729 日收益窗口存在缺失收盘价: {missing}")

    log_returns = np.log(price_matrix / price_matrix.shift(1)).dropna(how="any")
    if len(log_returns) != LOOKBACK_RETURNS:
        raise ValueError(f"Log-return matrix must have {LOOKBACK_RETURNS} rows, got {len(log_returns)}")

    annual_vol = log_returns.std(ddof=0) * np.sqrt(ANNUALIZATION_DAYS)
    vol_pass = annual_vol[(annual_vol > LOW_VOL_THRESHOLD) & (annual_vol < HIGH_VOL_THRESHOLD)]

    if vol_pass.empty:
        return empty_result(), {
            "signal_date": signal_date,
            "rebalance_date": rebalance_date,
            "vol_pass_count": 0,
            "low_corr_codes": [],
            "momentum_pass_count": 0,
        }

    corr = log_returns[vol_pass.index].corr().abs()
    if len(corr) == 1:
        avg_abs_corr = pd.Series(0.0, index=corr.index)
    else:
        avg_abs_corr = (corr.sum(axis=1) - 1.0) / (len(corr.columns) - 1)
    low_corr_codes = (
        avg_abs_corr.sort_values(kind="mergesort")
        .head(LOW_CORR_COUNT)
        .index.tolist()
    )

    rows: list[dict[str, object]] = []
    for code in low_corr_codes:
        score = calc_momentum_score(price_matrix[code].tail(MOMENTUM_PRICE_WINDOW))
        rows.append(
            {
                "signal_date": signal_date,
                "rebalance_date": rebalance_date,
                "etf_code": code,
                "etf_name": info.get(code, code),
                "annualized_volatility_729d": float(annual_vol[code]),
                "avg_abs_correlation": float(avg_abs_corr[code]),
                "momentum_score_25d": score,
            }
        )
    momentum = pd.DataFrame(rows)
    momentum = momentum[
        momentum["momentum_score_25d"].between(MOMENTUM_MIN, MOMENTUM_MAX, inclusive="neither")
    ].copy()
    metadata = {
        "signal_date": signal_date,
        "rebalance_date": rebalance_date,
        "vol_pass_count": int(len(vol_pass)),
        "low_corr_codes": low_corr_codes,
        "momentum_pass_count": int(len(momentum)),
    }
    if momentum.empty:
        return empty_result(), metadata

    selected = (
        momentum.sort_values(["momentum_score_25d", "etf_code"], ascending=[False, True], kind="mergesort")
        .head(1)
        .copy()
    )
    return selected[OUTPUT_COLUMNS].reset_index(drop=True), metadata


OUTPUT_COLUMNS = [
    "signal_date",
    "rebalance_date",
    "etf_code",
    "etf_name",
    "annualized_volatility_729d",
    "avg_abs_correlation",
    "momentum_score_25d",
]


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def format_output(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["annualized_volatility_729d", "avg_abs_correlation", "momentum_score_25d"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").round(8)
    return out


def main() -> pd.DataFrame:
    args = parse_args()
    as_of_date = pd.Timestamp(args.as_of_date).strftime("%Y-%m-%d")
    price_panel = fetch_price_panel(as_of_date)
    result, metadata = build_signal(price_panel)
    output = format_output(result)

    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"metadata={metadata}")
    print(output.to_string(index=False) if not output.empty else "<empty result>")
    return output


if __name__ == "__main__":
    main()
