#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


FUND_CODE = "016161"
AS_OF_DATE = "2025-08-22"
WINDOW = 252
ANNUAL_TRADING_DAYS = 252
OUTPUT_DIR = Path(__file__).resolve().parent
RESULT_CSV = OUTPUT_DIR / "result.csv"


NAV_SQL = f"""
WITH params AS (
    SELECT DATE('{AS_OF_DATE}') AS as_of_date
),
target_fund AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        COALESCE(sm.CHINAMEABBR, sm.SECUABBR, sm.CHINAME, fa.SECURITYCODE) AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    WHERE sm.SECUCODE = '{FUND_CODE}'
       OR fa.SECURITYCODE = '{FUND_CODE}'
    ORDER BY sm.SECUCODE
    LIMIT 1
),
nav_ranked AS (
    SELECT
        tf.fund_code,
        tf.fund_name,
        DATE(n.TRADINGDAY) AS trading_day,
        n.UNITNVRESTORED,
        ROW_NUMBER() OVER (
            PARTITION BY tf.INNERCODE, DATE(n.TRADINGDAY)
            ORDER BY n.UPDATETIME DESC, n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM target_fund tf
    JOIN mf_fundnetvalueretrans n
      ON n.INNERCODE = tf.INNERCODE
    CROSS JOIN params p
    WHERE n.UNITNVRESTORED IS NOT NULL
      AND n.TRADINGDAY <= p.as_of_date
      AND n.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND n.UPDATETIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
)
SELECT
    fund_code,
    fund_name,
    trading_day,
    UNITNVRESTORED
FROM nav_ranked
WHERE rn = 1
ORDER BY trading_day
"""

def fetch_nav() -> pd.DataFrame:
    nav_df = query_to_dataframe(NAV_SQL, max_rows=0, timeout=180)
    if nav_df.empty:
        raise ValueError("未取到 016161.OF 的复权单位净值数据。")

    nav_df["trading_day"] = pd.to_datetime(nav_df["trading_day"], errors="coerce")
    nav_df["UNITNVRESTORED"] = pd.to_numeric(nav_df["UNITNVRESTORED"], errors="coerce")
    nav_df = nav_df.dropna(subset=["trading_day", "UNITNVRESTORED"]).copy()
    nav_df = nav_df[nav_df["UNITNVRESTORED"] > 0].sort_values("trading_day").reset_index(drop=True)
    if len(nav_df) < WINDOW + 1:
        raise ValueError(f"数据量不足以计算滚动一年指标，至少需要 {WINDOW + 1} 条净值。")
    return nav_df


def calc_rolling_metrics(nav_df: pd.DataFrame) -> pd.DataFrame:
    metric_df = nav_df.copy()
    metric_df["daily_return"] = metric_df["UNITNVRESTORED"].pct_change()
    metric_df["rolling_return_pct"] = (
        metric_df["UNITNVRESTORED"] / metric_df["UNITNVRESTORED"].shift(WINDOW) - 1.0
    ) * 100.0
    metric_df["rolling_annual_vol_pct"] = (
        metric_df["daily_return"].rolling(window=WINDOW, min_periods=WINDOW).std(ddof=0)
        * np.sqrt(ANNUAL_TRADING_DAYS)
        * 100.0
    )
    metric_df["rolling_max_drawdown_pct"] = (
        metric_df["UNITNVRESTORED"]
        .rolling(window=WINDOW + 1, min_periods=WINDOW + 1)
        .apply(lambda values: abs(np.min(values / np.maximum.accumulate(values) - 1.0)) * 100.0, raw=True)
    )

    rolling_df = metric_df.dropna(
        subset=["rolling_return_pct", "rolling_annual_vol_pct", "rolling_max_drawdown_pct"]
    ).copy()
    if rolling_df.empty:
        raise ValueError("滚动窗口计算后没有有效结果。")
    return rolling_df[["trading_day", "rolling_return_pct", "rolling_annual_vol_pct", "rolling_max_drawdown_pct"]]


def build_result_table(nav_df: pd.DataFrame, rolling_df: pd.DataFrame) -> pd.DataFrame:
    latest = rolling_df.tail(1).iloc[0]
    return pd.DataFrame(
        [
            {
                "基金代码": nav_df["fund_code"].iloc[0],
                "基金简称": nav_df["fund_name"].iloc[0],
                "净值日期": latest["trading_day"].strftime("%Y-%m-%d"),
                "滚动一年收益率": latest["rolling_return_pct"],
                "年化波动率": latest["rolling_annual_vol_pct"],
                "最大回撤": latest["rolling_max_drawdown_pct"],
            }
        ]
    )


def main() -> pd.DataFrame:
    nav_df = fetch_nav()
    rolling_df = calc_rolling_metrics(nav_df)
    result_df = build_result_table(nav_df, rolling_df)

    result_df.to_csv(RESULT_CSV, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result_df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return result_df


if __name__ == "__main__":
    main()
