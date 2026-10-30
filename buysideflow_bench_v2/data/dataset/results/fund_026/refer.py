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


AS_OF_DATE = pd.Timestamp("2026-04-08")
AS_OF_NEXT_DAY = AS_OF_DATE + pd.Timedelta(days=1)
TARGET_FUND_CODE = "000477"

NAV_SQL = f"""
WITH params AS (
    SELECT
        DATE('{AS_OF_DATE:%Y-%m-%d}') AS as_of_date,
        DATE('{AS_OF_NEXT_DAY:%Y-%m-%d}') AS as_of_next_day,
        DATE_SUB(DATE('{AS_OF_DATE:%Y-%m-%d}'), INTERVAL 5 YEAR) AS start_date
),
target_fund AS (
    SELECT
        sm.INNERCODE,
        sm.SECUCODE AS fund_code,
        COALESCE(sm.CHINAMEABBR, sm.SECUABBR, sm.CHINAME) AS fund_name
    FROM secumain sm
    JOIN mf_fundarchives fa
      ON fa.INNERCODE = sm.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE sm.SECUCATEGORY = 8
      AND sm.SECUCODE = '{TARGET_FUND_CODE}'
      AND (fa.INSERTTIME IS NULL OR fa.INSERTTIME < p.as_of_next_day)
      AND (fa.EXPIREDATE IS NULL OR DATE(fa.EXPIREDATE) >= p.as_of_date)
    ORDER BY sm.INNERCODE
    LIMIT 1
),
nav_ranked AS (
    SELECT
        tf.fund_code,
        tf.fund_name,
        DATE(n.TRADINGDAY) AS trading_day,
        n.NVRDAILYGROWTHRATE / 100.0 AS daily_ret,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(n.TRADINGDAY)
            ORDER BY n.INSERTTIME DESC, n.UPDATETIME DESC, n.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans n
    JOIN target_fund tf
      ON tf.INNERCODE = n.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE n.TRADINGDAY >= p.start_date
      AND n.TRADINGDAY <= p.as_of_date
      AND n.NVRDAILYGROWTHRATE IS NOT NULL
      AND n.INSERTTIME < p.as_of_next_day
      AND n.UPDATETIME < p.as_of_next_day
)
SELECT fund_code, fund_name, trading_day, daily_ret
FROM nav_ranked
WHERE rn = 1
ORDER BY trading_day
"""

CALENDAR_SQL = f"""
SELECT DATE(TRADINGDATE) AS trading_day
FROM qt_tradingdaynew
WHERE SECUMARKET = 83
  AND IFTRADINGDAY = 1
  AND TRADINGDATE >= DATE_SUB(DATE('{AS_OF_DATE:%Y-%m-%d}'), INTERVAL 5 YEAR)
  AND TRADINGDATE <= DATE('{AS_OF_DATE:%Y-%m-%d}')
ORDER BY TRADINGDATE
"""


def build_comparable_quarters(nav_df: pd.DataFrame, trading_days: pd.Series) -> pd.DataFrame:
    nav_df = nav_df.copy()
    nav_by_date = nav_df.drop_duplicates("trading_day", keep="last").set_index("trading_day")
    calendar_df = pd.DataFrame({"trading_day": pd.to_datetime(trading_days).drop_duplicates().sort_values()})
    calendar_df["quarter"] = calendar_df["trading_day"].dt.to_period("Q")

    completed_quarters = sorted(
        q for q in calendar_df["quarter"].dropna().unique()
        if q.end_time.normalize() <= AS_OF_DATE.normalize()
    )

    rows = []
    for quarter in reversed(completed_quarters):
        next_quarter = quarter + 1
        quarter_dates = calendar_df.loc[calendar_df["quarter"] == quarter, "trading_day"].tail(3)
        next_dates = calendar_df.loc[calendar_df["quarter"] == next_quarter, "trading_day"].head(3)

        if len(quarter_dates) != 3 or len(next_dates) != 3:
            continue
        if not quarter_dates.isin(nav_by_date.index).all() or not next_dates.isin(nav_by_date.index).all():
            continue

        quarter_last3 = nav_by_date.loc[quarter_dates]
        next_first3 = nav_by_date.loc[next_dates]

        rows.append(
            {
                "季度": str(quarter),
                "季末后三日均值": quarter_last3["daily_ret"].mean(),
                "下季初三日均值": next_first3["daily_ret"].mean(),
                "差值": quarter_last3["daily_ret"].mean() - next_first3["daily_ret"].mean(),
                "季末后三日": ",".join(quarter_dates.dt.strftime("%Y-%m-%d")),
                "下季初三日": ",".join(next_dates.dt.strftime("%Y-%m-%d")),
            }
        )

        if len(rows) == 12:
            break

    if len(rows) < 12:
        raise ValueError(f"两边数据完整的可比季度仅 {len(rows)} 个，不足 12 个")

    return pd.DataFrame(list(reversed(rows)))


def main() -> pd.DataFrame:
    nav_df = query_to_dataframe(NAV_SQL, max_rows=300000, timeout=300)
    calendar_df = query_to_dataframe(CALENDAR_SQL, max_rows=5000, timeout=120)
    if nav_df.empty:
        raise ValueError("复权净值日收益序列为空")
    if calendar_df.empty:
        raise ValueError("A股交易日历为空")

    nav_df["trading_day"] = pd.to_datetime(nav_df["trading_day"], errors="coerce")
    nav_df["daily_ret"] = pd.to_numeric(nav_df["daily_ret"], errors="coerce")
    nav_df = nav_df.dropna(subset=["trading_day", "daily_ret"]).sort_values("trading_day").reset_index(drop=True)

    if nav_df.empty:
        raise ValueError("清洗后复权净值日收益序列为空")

    calendar_df["trading_day"] = pd.to_datetime(calendar_df["trading_day"], errors="coerce")
    calendar_df = calendar_df.dropna(subset=["trading_day"])

    fund_code = str(nav_df.iloc[0]["fund_code"])
    fund_name = str(nav_df.iloc[0]["fund_name"])
    pair_df = build_comparable_quarters(nav_df, calendar_df["trading_day"])

    avg_diff = float(pair_df["差值"].mean())
    positive_ratio = float((pair_df["差值"] > 0).mean())
    max_diff = float(pair_df["差值"].max())
    flag = "季末收益偏高" if avg_diff > 0 and positive_ratio >= 0.75 else "未标记"

    detail_df = pd.DataFrame(
        {
            "结果类型": "明细",
            "基金代码": fund_code,
            "基金简称": fund_name,
            "季度": pair_df["季度"],
            "差值": (pair_df["差值"] * 100).round(2),
            "差值均值": np.nan,
            "正差值占比": np.nan,
            "最大差值": np.nan,
            "判断结果": "",
        }
    )

    summary_df = pd.DataFrame(
        [
            {
                "结果类型": "汇总",
                "基金代码": fund_code,
                "基金简称": fund_name,
                "季度": "",
                "差值": np.nan,
                "差值均值": round(avg_diff * 100, 2),
                "正差值占比": round(positive_ratio * 100, 2),
                "最大差值": round(max_diff * 100, 2),
                "判断结果": flag,
            }
        ]
    )

    result_df = pd.concat([detail_df, summary_df], ignore_index=True)
    print(result_df.to_string(index=False))
    return result_df


if __name__ == "__main__":
    output = main()
    output.to_csv("result.csv", index=False, encoding="utf-8-sig", float_format="%.2f")
