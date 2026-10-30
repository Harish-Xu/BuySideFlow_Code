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


FUGUO_ADVISOR_CODE = 41514
ACTIVE_EQUITY_CODES = (1101, 1201, 1202, 1203)
YEARS = (2023, 2024, 2025)
OUTPUT_DIR = Path(__file__).resolve().parent
RESULT_CSV = OUTPUT_DIR / "result.csv"


def pct2(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):.2f}"


YEARS_SQL = """
    SELECT 2023 AS stat_year, DATE('2022-12-31') AS prev_end_date,
           DATE('2023-01-01') AS year_start, DATE('2023-12-31') AS year_end
    UNION ALL
    SELECT 2024, DATE('2023-12-31'), DATE('2024-01-01'), DATE('2024-12-31')
    UNION ALL
    SELECT 2025, DATE('2024-12-31'), DATE('2025-01-01'), DATE('2025-12-31')
"""


FUND_POOL_CTES = f"""
WITH years AS (
{YEARS_SQL}
),
type_ranked AS (
    SELECT
        y.stat_year,
        t.INNERCODE,
        t.SECASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY y.stat_year, t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS rn
    FROM years y
    JOIN mf_jyfundtype t
      ON t.STANDARD = 75
     AND t.EFFECTIVEDATE <= y.year_end
     AND (t.CANCELDATE IS NULL OR t.CANCELDATE > y.year_end)
     AND t.INSERTTIME < DATE_ADD(y.year_end, INTERVAL 1 DAY)
),
peer_funds AS (
    SELECT
        y.stat_year,
        y.prev_end_date,
        y.year_start,
        y.year_end,
        fa.INNERCODE,
        fa.INVESTADVISORCODE
    FROM years y
    JOIN mf_fundarchives fa
      ON 1 = 1
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN type_ranked tr
      ON tr.stat_year = y.stat_year
     AND tr.INNERCODE = fa.INNERCODE
     AND tr.rn = 1
    WHERE tr.SECASSETCATCODE IN ({", ".join(str(code) for code in ACTIVE_EQUITY_CODES)})
      AND fa.INVESTMENTTYPE = 16
      AND fa.TYPE IN (2, 3, 7)
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
      AND (fa.ESTABLISHMENTDATE IS NULL OR fa.ESTABLISHMENTDATE <= y.prev_end_date)
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > y.year_end)
)
"""


YEAR_END_RETURN_SQL = f"""
{FUND_POOL_CTES},
nav_labeled AS (
    SELECT
        pf.stat_year,
        pf.INNERCODE,
        pf.INVESTADVISORCODE,
        CASE WHEN n.TRADINGDAY <= pf.prev_end_date THEN 'start' ELSE 'end' END AS nav_side,
        DATE(n.TRADINGDAY) AS trading_day,
        n.UNITNVRESTORED,
        ROW_NUMBER() OVER (
            PARTITION BY
                pf.stat_year,
                pf.INNERCODE,
                CASE WHEN n.TRADINGDAY <= pf.prev_end_date THEN 'start' ELSE 'end' END,
                DATE(n.TRADINGDAY)
            ORDER BY n.UPDATETIME DESC, n.INSERTTIME DESC, n.ID DESC
        ) AS day_rn
    FROM peer_funds pf
    JOIN mf_fundnetvalueretrans n
      ON n.INNERCODE = pf.INNERCODE
     AND n.UNITNVRESTORED IS NOT NULL
     AND (
          (n.TRADINGDAY > DATE_SUB(pf.prev_end_date, INTERVAL 40 DAY)
           AND n.TRADINGDAY <= pf.prev_end_date)
       OR (n.TRADINGDAY > DATE_SUB(pf.year_end, INTERVAL 40 DAY)
           AND n.TRADINGDAY <= pf.year_end)
     )
     AND n.INSERTTIME < DATE_ADD(pf.year_end, INTERVAL 1 DAY)
),
nav_ranked AS (
    SELECT
        nl.*,
        ROW_NUMBER() OVER (
            PARTITION BY nl.stat_year, nl.INNERCODE, nl.nav_side
            ORDER BY nl.trading_day DESC
        ) AS side_rn
    FROM nav_labeled nl
    WHERE nl.day_rn = 1
)
SELECT
    stat_year,
    INNERCODE,
    INVESTADVISORCODE,
    MAX(CASE WHEN nav_side = 'start' THEN UNITNVRESTORED END) AS start_nav,
    MAX(CASE WHEN nav_side = 'end' THEN UNITNVRESTORED END) AS end_nav,
    MAX(CASE WHEN nav_side = 'start' THEN trading_day END) AS start_date,
    MAX(CASE WHEN nav_side = 'end' THEN trading_day END) AS end_date
FROM nav_ranked
WHERE side_rn = 1
GROUP BY stat_year, INNERCODE, INVESTADVISORCODE
HAVING start_nav IS NOT NULL
   AND end_nav IS NOT NULL
ORDER BY stat_year, INNERCODE
"""


FUGUO_DAILY_NAV_SQL = f"""
{FUND_POOL_CTES},
company_funds AS (
    SELECT *
    FROM peer_funds
    WHERE INVESTADVISORCODE = {FUGUO_ADVISOR_CODE}
),
nav_ranked AS (
    SELECT
        cf.stat_year,
        cf.prev_end_date,
        cf.year_start,
        cf.year_end,
        cf.INNERCODE,
        DATE(n.TRADINGDAY) AS trading_day,
        n.UNITNVRESTORED,
        ROW_NUMBER() OVER (
            PARTITION BY cf.stat_year, cf.INNERCODE, DATE(n.TRADINGDAY)
            ORDER BY n.UPDATETIME DESC, n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM company_funds cf
    JOIN mf_fundnetvalueretrans n
      ON n.INNERCODE = cf.INNERCODE
     AND n.UNITNVRESTORED IS NOT NULL
     AND n.TRADINGDAY > DATE_SUB(cf.prev_end_date, INTERVAL 40 DAY)
     AND n.TRADINGDAY <= cf.year_end
     AND n.INSERTTIME < DATE_ADD(cf.year_end, INTERVAL 1 DAY)
)
SELECT
    stat_year,
    prev_end_date,
    year_start,
    year_end,
    INNERCODE,
    trading_day,
    UNITNVRESTORED
FROM nav_ranked
WHERE rn = 1
ORDER BY stat_year, INNERCODE, trading_day
"""


def fetch_yearly_returns() -> pd.DataFrame:
    df = query_to_dataframe(YEAR_END_RETURN_SQL, max_rows=0, timeout=300)
    if df.empty:
        raise ValueError("未取到主动权益主基金年度起止净值。")
    for column in ["stat_year", "INNERCODE"]:
        df[column] = pd.to_numeric(df[column], errors="raise").astype(int)
    df["INVESTADVISORCODE"] = pd.to_numeric(df["INVESTADVISORCODE"], errors="coerce").astype("Int64")
    for column in ["start_nav", "end_nav"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    for column in ["start_date", "end_date"]:
        df[column] = pd.to_datetime(df[column], errors="coerce")
    df = df.dropna(subset=["start_nav", "end_nav", "start_date", "end_date"])
    df = df[(df["start_nav"] > 0) & (df["end_nav"] > 0)].copy()
    df["annual_return_pct"] = (df["end_nav"] / df["start_nav"] - 1.0) * 100.0
    df["return_rank_pct"] = (
        df.groupby("stat_year")["annual_return_pct"].rank(method="min", pct=True, ascending=True) * 100.0
    )
    return df


def fetch_fuguo_daily_nav() -> pd.DataFrame:
    df = query_to_dataframe(FUGUO_DAILY_NAV_SQL, max_rows=0, timeout=300)
    if df.empty:
        raise ValueError("未取到富国主动权益主基金年度内复权净值。")
    for column in ["stat_year", "INNERCODE"]:
        df[column] = pd.to_numeric(df[column], errors="raise").astype(int)
    for column in ["prev_end_date", "year_start", "year_end", "trading_day"]:
        df[column] = pd.to_datetime(df[column], errors="coerce")
    df["UNITNVRESTORED"] = pd.to_numeric(df["UNITNVRESTORED"], errors="coerce")
    return df.dropna(subset=["trading_day", "UNITNVRESTORED"]).sort_values(
        ["stat_year", "INNERCODE", "trading_day"]
    )


def calc_fuguo_drawdowns(yearly_returns: pd.DataFrame, daily_nav: pd.DataFrame) -> pd.DataFrame:
    fuguo_complete = yearly_returns[
        yearly_returns["INVESTADVISORCODE"] == FUGUO_ADVISOR_CODE
    ][["stat_year", "INNERCODE", "start_date", "end_date"]].copy()
    if fuguo_complete.empty:
        raise ValueError("年度完整业绩样本中没有富国主动权益基金。")

    rows: list[dict[str, float | int]] = []
    for (stat_year, innercode), group in daily_nav.groupby(["stat_year", "INNERCODE"], sort=False):
        matched = fuguo_complete[
            (fuguo_complete["stat_year"] == stat_year)
            & (fuguo_complete["INNERCODE"] == innercode)
        ]
        if matched.empty:
            continue

        start_date = matched.iloc[0]["start_date"]
        end_date = matched.iloc[0]["end_date"]
        year_start = pd.Timestamp(year=int(stat_year), month=1, day=1)
        series = group[
            (group["trading_day"] == start_date)
            | ((group["trading_day"] >= year_start) & (group["trading_day"] <= end_date))
        ].sort_values("trading_day")
        if len(series) < 2:
            continue

        nav = series["UNITNVRESTORED"]
        max_drawdown_pct = abs((nav / nav.cummax() - 1.0).min()) * 100.0
        rows.append(
            {
                "stat_year": int(stat_year),
                "INNERCODE": int(innercode),
                "max_drawdown_pct": float(max_drawdown_pct),
                "nav_count": int(len(series)),
            }
        )

    result = pd.DataFrame(rows)
    if result.empty:
        raise ValueError("富国主动权益基金年度最大回撤均不可计算。")
    return result


def build_result() -> pd.DataFrame:
    yearly_returns = fetch_yearly_returns()
    daily_nav = fetch_fuguo_daily_nav()
    drawdowns = calc_fuguo_drawdowns(yearly_returns, daily_nav)

    fuguo_metrics = yearly_returns[
        yearly_returns["INVESTADVISORCODE"] == FUGUO_ADVISOR_CODE
    ].merge(drawdowns[["stat_year", "INNERCODE", "max_drawdown_pct"]], on=["stat_year", "INNERCODE"], how="inner")
    if fuguo_metrics.empty:
        raise ValueError("富国主动权益基金年度完整业绩样本为空。")

    summary = (
        fuguo_metrics.groupby("stat_year", as_index=False)
        .agg(
            fund_count=("INNERCODE", "nunique"),
            avg_return_pct=("annual_return_pct", "mean"),
            avg_max_drawdown_pct=("max_drawdown_pct", "mean"),
            avg_return_rank_pct=("return_rank_pct", "mean"),
        )
        .sort_values("stat_year")
    )
    summary = summary[summary["stat_year"].isin(YEARS)].copy()

    output = pd.DataFrame(
        {
            "年份": summary["stat_year"].astype(int),
            "基金数量": summary["fund_count"].astype(int),
            "平均收益率(%)": summary["avg_return_pct"].map(pct2),
            "平均最大回撤(%)": summary["avg_max_drawdown_pct"].map(pct2),
            "平均收益排名分位数(%)": summary["avg_return_rank_pct"].map(pct2),
        }
    ).reset_index(drop=True)
    return output


def main() -> pd.DataFrame:
    result = build_result()
    result.to_csv(RESULT_CSV, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
