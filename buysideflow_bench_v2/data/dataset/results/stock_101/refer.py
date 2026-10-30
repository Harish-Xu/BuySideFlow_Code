#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = pd.Timestamp("2025-12-31")
HISTORY_WINDOW = 252
RETURN_WINDOW = 20


def percentile_rank(series: pd.Series, value: float) -> float:
    valid = series.dropna()
    if valid.empty or pd.isna(value):
        return float("nan")
    return float((valid <= value).mean())


def build_result(raw_df: pd.DataFrame) -> pd.DataFrame:
    if raw_df.empty:
        raise ValueError("No industry index quote data returned")

    df = raw_df.copy()
    df["trading_day"] = pd.to_datetime(df["trading_day"])
    df["close_price"] = pd.to_numeric(df["close_price"], errors="coerce")
    df["turnover_rate"] = pd.to_numeric(df["turnover_rate"], errors="coerce")
    df = df.sort_values(["industry_code", "trading_day"]).reset_index(drop=True)
    latest_trade_day = min(df["trading_day"].max(), AS_OF_DATE)

    rows: list[dict[str, object]] = []
    for (code, name), group in df.groupby(["industry_code", "industry_name"], sort=True):
        g = group.sort_values("trading_day").copy()
        g["return_20d"] = g["close_price"] / g["close_price"].shift(RETURN_WINDOW) - 1.0
        g = g[g["trading_day"] <= latest_trade_day].copy()
        latest_rows = g[g["trading_day"] == latest_trade_day]
        if latest_rows.empty:
            continue
        latest = latest_rows.iloc[-1]

        turnover_history = g["turnover_rate"].dropna().tail(HISTORY_WINDOW)
        return_history = g["return_20d"].dropna().tail(HISTORY_WINDOW)
        if len(turnover_history) < HISTORY_WINDOW or len(return_history) < HISTORY_WINDOW:
            continue
        turnover_pct = percentile_rank(turnover_history, latest["turnover_rate"])
        return_20d_pct = percentile_rank(return_history, latest["return_20d"])
        crowding_score = 0.5 * turnover_pct + 0.5 * return_20d_pct
        rows.append(
            {
                "交易日": latest["trading_day"].strftime("%Y-%m-%d"),
                "行业代码": code,
                "行业名称": name,
                "换手率分位": turnover_pct,
                "20日涨幅分位": return_20d_pct,
                "量价拥挤分数": crowding_score,
            }
        )

    result = pd.DataFrame(rows)
    if result.empty:
        raise ValueError("No crowding result rows generated")
    numeric_cols = ["换手率分位", "20日涨幅分位", "量价拥挤分数"]
    result[numeric_cols] = result[numeric_cols].round(6)
    return result.sort_values(["量价拥挤分数", "行业代码"], ascending=[False, True]).reset_index(drop=True)


def main() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    raw_df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if raw_df is None:
        raise ValueError("SQL execution returned None")
    result = build_result(raw_df)
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    main()
