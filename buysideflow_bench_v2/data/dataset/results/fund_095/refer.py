#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = "2026-01-03"
START_DATE = "2025-01-01"
END_DATE_EXCLUSIVE = "2026-01-01"
TRADING_DAYS_PER_YEAR = 252
MAX_SINGLE_WEIGHT = 0.60

ASSETS = [
    ("159941", "纳指ETF"),
    ("518880", "黄金ETF"),
    ("159985", "豆粕ETF"),
    ("511220", "城投债ETF"),
]
ASSET_CODES = [code for code, _ in ASSETS]


def fetch_common_returns(as_of_date: str = AS_OF_DATE) -> pd.DataFrame:
    sql = f"""
WITH nav_ranked AS (
    SELECT
        s.SECUCODE,
        DATE(f.TRADINGDAY) AS trading_day,
        f.NVRDAILYGROWTHRATE,
        ROW_NUMBER() OVER (
            PARTITION BY s.SECUCODE, DATE(f.TRADINGDAY)
            ORDER BY f.UPDATETIME DESC, f.INSERTTIME DESC, f.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans f
    JOIN secumain s
      ON s.INNERCODE = f.INNERCODE
    WHERE s.SECUCATEGORY = 8
      AND s.SECUCODE IN ('159941', '518880', '159985', '511220')
      AND f.TRADINGDAY >= DATE('{START_DATE}')
      AND f.TRADINGDAY < DATE('{END_DATE_EXCLUSIVE}')
      AND f.UNITNVRESTORED IS NOT NULL
      AND f.NVRDAILYGROWTHRATE IS NOT NULL
      AND f.INSERTTIME < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
      AND f.UPDATETIME < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
)
SELECT
    SECUCODE,
    trading_day,
    NVRDAILYGROWTHRATE
FROM nav_ranked
WHERE rn = 1
ORDER BY SECUCODE, trading_day;
"""
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        raise ValueError("未取得观察日前可见的 ETF 复权净值日收益数据")

    df = df.copy()
    df["trading_day"] = pd.to_datetime(df["trading_day"], errors="coerce")
    df["NVRDAILYGROWTHRATE"] = pd.to_numeric(df["NVRDAILYGROWTHRATE"], errors="coerce")
    df = df.dropna(subset=["trading_day", "NVRDAILYGROWTHRATE"]).copy()
    aligned = (
        df.pivot(index="trading_day", columns="SECUCODE", values="NVRDAILYGROWTHRATE")
        .reindex(columns=ASSET_CODES)
        .dropna(how="any")
        .sort_index()
    )
    if aligned.empty:
        raise ValueError("四只 ETF 在 2025 年没有共同可见净值收益样本")
    return aligned / 100.0


def portfolio_volatility(weights: np.ndarray, covariance: np.ndarray) -> float:
    variance = float(weights @ covariance @ weights)
    if variance <= 0:
        raise ValueError("协方差矩阵对应的组合方差非正")
    return float(np.sqrt(variance))


def marginal_risk_contribution(weights: np.ndarray, covariance: np.ndarray) -> np.ndarray:
    sigma = portfolio_volatility(weights, covariance)
    return covariance @ weights / sigma


def risk_contribution(weights: np.ndarray, covariance: np.ndarray) -> np.ndarray:
    return weights * marginal_risk_contribution(weights, covariance)


def erc_objective(weights: np.ndarray, covariance: np.ndarray) -> float:
    rc = risk_contribution(weights, covariance)
    return float(np.sum((rc - rc.mean()) ** 2))


def solve_erc(covariance: np.ndarray, upper_bound: float) -> dict[str, np.ndarray | float]:
    n_assets = covariance.shape[0]
    initial_weights = np.full(n_assets, 1.0 / n_assets)
    result = minimize(
        erc_objective,
        initial_weights,
        args=(covariance,),
        method="SLSQP",
        bounds=[(0.0, upper_bound)] * n_assets,
        constraints=[{"type": "eq", "fun": lambda weights: np.sum(weights) - 1.0}],
        options={"ftol": 1e-15, "maxiter": 1000, "disp": False},
    )
    if not result.success:
        raise RuntimeError(f"ERC 优化失败: {result.message}")

    weights = np.clip(result.x, 0.0, upper_bound)
    weights = weights / weights.sum()
    daily_vol = portfolio_volatility(weights, covariance)
    daily_rc = risk_contribution(weights, covariance)
    annualized_vol = daily_vol * np.sqrt(TRADING_DAYS_PER_YEAR)
    annualized_rc = daily_rc * np.sqrt(TRADING_DAYS_PER_YEAR)
    return {
        "weights": weights,
        "daily_rc": daily_rc,
        "annualized_vol": annualized_vol,
        "annualized_rc": annualized_rc,
        "max_weight": float(weights.max()),
        "rc_dispersion": float(np.std(annualized_rc)),
        "objective_value": erc_objective(weights, covariance),
    }


def build_result(returns: pd.DataFrame) -> pd.DataFrame:
    covariance = returns.cov().to_numpy()
    primary = solve_erc(covariance, upper_bound=1.0)
    triggered = primary["max_weight"] > MAX_SINGLE_WEIGHT + 1e-10
    selected = solve_erc(covariance, upper_bound=MAX_SINGLE_WEIGHT) if triggered else primary

    row: dict[str, object] = {
        "共同样本天数": int(len(returns)),
        "是否触发上限": "是" if triggered else "否",
        "组合年化波动率": f"{float(selected['annualized_vol']) * 100:.2f}",
        "风险贡献离散度": f"{float(selected['rc_dispersion']) * 100:.2f}",
    }
    weights = selected["weights"]
    annualized_rc = selected["annualized_rc"]
    for idx, (code, name) in enumerate(ASSETS):
        row[f"{name}({code})权重"] = f"{float(weights[idx]) * 100:.2f}"
        row[f"{name}({code})风险贡献"] = f"{float(annualized_rc[idx]) * 100:.2f}"
    return pd.DataFrame([row])


def main() -> pd.DataFrame:
    output_path = Path(__file__).with_name("result.csv")
    returns = fetch_common_returns()
    result = build_result(returns)
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
