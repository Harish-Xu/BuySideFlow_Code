#!/usr/bin/env python3
"""10 只 ETF 的复权净值逆方差信号和 Simple EPO 权重。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.linalg import solve

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = "2025-12-31"
ETF_CODES = ("159985", "518800", "513000", "513100", "513030",
             "159930", "159981", "159980", "510880", "510230")
OUTPUT_COLUMNS = ["计算日", "ETF代码", "ETF简称", "250日年化波动率", "逆方差权重", "Simple EPO权重"]
WINDOW_DAYS = 251
SHRINKAGE = 0.6


def load_nav_window() -> pd.DataFrame:
    codes = ",".join(f"'{code}'" for code in ETF_CODES)
    sql = f"""
        WITH fund_pool AS (
            SELECT s.INNERCODE, s.SECUCODE AS fund_code, s.SECUABBR AS fund_name
            FROM secumain s
            JOIN mf_fundarchives f ON f.INNERCODE = s.INNERCODE
            WHERE s.SECUCATEGORY = 8 AND f.TYPE = 4
              AND s.SECUCODE IN ({codes})
        ),
        visible_nav AS (
            SELECT n.INNERCODE, DATE(n.TRADINGDAY) AS trading_day,
                   n.UNITNVRESTORED AS nav,
                   ROW_NUMBER() OVER (
                       PARTITION BY n.INNERCODE, DATE(n.TRADINGDAY)
                       ORDER BY n.INSERTTIME DESC, n.UPDATETIME DESC, n.ID DESC
                   ) AS rn
            FROM mf_fundnetvalueretrans n
            JOIN fund_pool f ON f.INNERCODE = n.INNERCODE
            WHERE n.TRADINGDAY <= '{AS_OF_DATE}'
              AND n.INSERTTIME < DATE_ADD('{AS_OF_DATE}', INTERVAL 1 DAY)
        ),
        trading_days AS (
            SELECT DISTINCT DATE(TRADINGDATE) AS trading_day
            FROM qt_tradingdaynew
            WHERE IFTRADINGDAY = 1 AND SECUMARKET IN (83, 90)
              AND TRADINGDATE <= '{AS_OF_DATE}'
        ),
        calculation_day AS (
            SELECT MAX(trading_day) AS calculation_day
            FROM (
                SELECT t.trading_day
                FROM trading_days t
                JOIN visible_nav n ON n.trading_day = t.trading_day AND n.rn = 1
                WHERE n.nav > 0
                GROUP BY t.trading_day
                HAVING COUNT(DISTINCT n.INNERCODE) = {len(ETF_CODES)}
            ) complete_days
        ),
        window_days AS (
            SELECT trading_day
            FROM (
                SELECT t.trading_day,
                       ROW_NUMBER() OVER (ORDER BY t.trading_day DESC) AS rn
                FROM trading_days t
                JOIN calculation_day d ON t.trading_day <= d.calculation_day
            ) x
            WHERE rn <= {WINDOW_DAYS}
        )
        SELECT d.calculation_day, w.trading_day,
               f.fund_code, f.fund_name, n.nav
        FROM calculation_day d
        JOIN window_days w
        JOIN fund_pool f
        LEFT JOIN visible_nav n
          ON n.INNERCODE = f.INNERCODE
         AND n.trading_day = w.trading_day AND n.rn = 1
        ORDER BY w.trading_day, f.fund_code
    """
    frame = query_to_dataframe(sql, max_rows=0, timeout=600)
    if frame is None:
        raise RuntimeError("SQL 未返回结果")
    return frame


def simple_epo(returns: pd.DataFrame, signal: np.ndarray) -> np.ndarray:
    covariance = returns.cov(ddof=0).to_numpy(dtype=float)
    correlation = returns.corr().to_numpy(dtype=float)
    volatility = np.sqrt(np.diag(covariance))
    shrunk = np.diag(volatility) @ (
        (1.0 - SHRINKAGE) * correlation + SHRINKAGE * np.eye(len(signal))
    ) @ np.diag(volatility)
    raw = solve(shrunk, signal, assume_a="pos") / 10.0
    clipped = np.clip(raw, 0.0, None)
    if clipped.sum() <= 0:
        raise ValueError("Simple EPO 无正权重")
    return clipped / clipped.sum()


def build_result(panel: pd.DataFrame) -> pd.DataFrame:
    empty = pd.DataFrame(columns=OUTPUT_COLUMNS)
    if panel.empty:
        return empty
    panel = panel.copy()
    panel["trading_day"] = pd.to_datetime(panel["trading_day"])
    panel["nav"] = pd.to_numeric(panel["nav"], errors="coerce")
    matrix = panel.pivot(index="trading_day", columns="fund_code", values="nav").sort_index()
    if len(matrix) != WINDOW_DAYS:
        return empty
    valid = matrix.notna().all() & matrix.gt(0).all()
    matrix = matrix.loc[:, valid]
    if matrix.empty:
        return empty
    returns = matrix.pct_change(fill_method=None).iloc[1:]
    if len(returns) != WINDOW_DAYS - 1 or returns.isna().any().any():
        return empty
    variance = returns.var(ddof=0)
    returns = returns.loc[:, variance.gt(0)]
    if returns.empty:
        return empty
    variance = variance.loc[returns.columns]
    signal = (1.0 / variance) / (1.0 / variance).sum()
    weights = simple_epo(returns, signal.to_numpy(dtype=float))
    names = panel.drop_duplicates("fund_code").set_index("fund_code")["fund_name"]
    result = pd.DataFrame({
        "计算日": str(panel["calculation_day"].iloc[0]),
        "ETF代码": returns.columns,
        "ETF简称": names.loc[returns.columns].to_numpy(),
        "250日年化波动率": returns.std(ddof=0).to_numpy() * np.sqrt(252.0),
        "逆方差权重": signal.to_numpy(),
        "Simple EPO权重": weights,
    })
    return result.sort_values("ETF代码").reset_index(drop=True)[OUTPUT_COLUMNS]


def main() -> pd.DataFrame:
    result = build_result(load_nav_window())
    path = Path(__file__).with_name("result.csv")
    result.to_csv(path, index=False, encoding="utf-8-sig", float_format="%.6f")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    main()
