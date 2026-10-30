#!/usr/bin/env python3
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


SCRIPT_DIR = Path(__file__).resolve().parent
AS_OF_DATE = "2025-11-13"
FUND_CODE = "630002"
REPORT_COUNT = 12
TRADING_DAYS_PER_YEAR = 252


def get_fund_innercode() -> int:
    sql = f"""
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCODE = '{FUND_CODE}'
      AND SECUCATEGORY = 8
    ORDER BY INNERCODE
    LIMIT 1
    """
    fund_df = query_to_dataframe(sql, max_rows=10, timeout=120)
    if fund_df.empty:
        raise ValueError(f"未找到基金 {FUND_CODE}.OF")
    return int(fund_df.loc[0, "INNERCODE"])


def fetch_top10_weights(fund_innercode: int) -> pd.DataFrame:
    sql = f"""
    WITH params AS (
        SELECT DATE('{AS_OF_DATE}') AS as_of_date
    ),
    holding_latest AS (
        SELECT
            k.REPORTDATE,
            k.SERIALNUMBER,
            k.RATIOINNV,
            ROW_NUMBER() OVER (
                PARTITION BY k.REPORTDATE, k.SERIALNUMBER
                ORDER BY k.INFOPUBLDATE DESC, k.ID DESC
            ) AS rn
        FROM mf_keystockportfolio k
        JOIN params p
          ON k.REPORTDATE <= p.as_of_date
         AND k.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE k.INNERCODE = {fund_innercode}
          AND k.SERIALNUMBER BETWEEN 1 AND 10
    ),
    latest_reports AS (
        SELECT DISTINCT REPORTDATE
        FROM holding_latest
        WHERE rn = 1
        ORDER BY REPORTDATE DESC
        LIMIT {REPORT_COUNT}
    ),
    quarter_concentration AS (
        SELECT
            h.REPORTDATE,
            SUM(h.RATIOINNV) AS top10_weight_in_nv,
            COUNT(DISTINCT h.SERIALNUMBER) AS holding_count
        FROM holding_latest h
        JOIN latest_reports r ON r.REPORTDATE = h.REPORTDATE
        WHERE h.rn = 1
        GROUP BY h.REPORTDATE
        HAVING holding_count = 10
           AND COUNT(h.RATIOINNV) = 10
    )
    SELECT
        DATE(REPORTDATE) AS report_date,
        top10_weight_in_nv
    FROM quarter_concentration
    ORDER BY REPORTDATE ASC
    """
    holdings_df = query_to_dataframe(sql, max_rows=200, timeout=180)
    holdings_df["report_date"] = pd.to_datetime(holdings_df["report_date"])
    holdings_df["top10_weight_in_nv"] = pd.to_numeric(holdings_df["top10_weight_in_nv"], errors="coerce")
    holdings_df = holdings_df.dropna(subset=["top10_weight_in_nv"]).sort_values("report_date")
    if len(holdings_df) != REPORT_COUNT:
        raise ValueError(f"最近 {REPORT_COUNT} 个已披露季度持仓数据不完整，当前有效季度 {len(holdings_df)} 个")
    return holdings_df.reset_index(drop=True)


def fetch_nav(fund_innercode: int, start_date: pd.Timestamp, end_date: pd.Timestamp) -> pd.DataFrame:
    sql = f"""
    WITH params AS (
        SELECT DATE('{AS_OF_DATE}') AS as_of_date
    ),
    calendar_seed AS (
        SELECT MAX(DATE(TRADINGDATE)) AS trading_day
        FROM qt_tradingdaynew
        WHERE SECUMARKET IN (83, 90)
          AND IFTRADINGDAY = 1
          AND TRADINGDATE < '{start_date:%Y-%m-%d}'
    ),
    calendar_window AS (
        SELECT DISTINCT DATE(TRADINGDATE) AS trading_day
        FROM qt_tradingdaynew
        WHERE SECUMARKET IN (83, 90)
          AND IFTRADINGDAY = 1
          AND TRADINGDATE >= '{start_date:%Y-%m-%d}'
          AND TRADINGDATE <= '{end_date:%Y-%m-%d}'
        UNION
        SELECT trading_day FROM calendar_seed WHERE trading_day IS NOT NULL
    ),
    nav_latest AS (
        SELECT
            nv.TRADINGDAY,
            nv.UNITNVRESTORED,
            ROW_NUMBER() OVER (
                PARTITION BY nv.TRADINGDAY
                ORDER BY nv.INSERTTIME DESC, nv.ID DESC
            ) AS rn
        FROM mf_fundnetvalueretrans nv
        JOIN calendar_window c ON nv.TRADINGDAY = c.trading_day
        JOIN params p
          ON nv.TRADINGDAY <= '{end_date.strftime("%Y-%m-%d")}'
         AND nv.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE nv.INNERCODE = {fund_innercode}
    )
    SELECT
        c.trading_day,
        nv.UNITNVRESTORED
    FROM calendar_window c
    LEFT JOIN nav_latest nv ON nv.TRADINGDAY = c.trading_day AND nv.rn = 1
    ORDER BY c.trading_day ASC
    """
    nav_df = query_to_dataframe(sql, max_rows=30000, timeout=180)
    if nav_df.empty:
        raise ValueError("未获取到复权单位净值数据")

    nav_df["trading_day"] = pd.to_datetime(nav_df["trading_day"])
    nav_df["UNITNVRESTORED"] = pd.to_numeric(nav_df["UNITNVRESTORED"], errors="coerce")
    nav_df = nav_df.sort_values("trading_day").reset_index(drop=True)
    valid = np.isfinite(nav_df["UNITNVRESTORED"]) & nav_df["UNITNVRESTORED"].gt(0)
    nav_df.loc[~valid, "UNITNVRESTORED"] = np.nan
    # Keep missing market dates until after pct_change so returns never bridge gaps.
    nav_df["daily_return"] = nav_df["UNITNVRESTORED"].pct_change(fill_method=None)
    return nav_df.loc[
        nav_df["trading_day"].ge(start_date) & np.isfinite(nav_df["daily_return"])
    ].reset_index(drop=True)


def build_quarter_metrics(holdings_df: pd.DataFrame, nav_df: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, float | str]] = []
    for _, row in holdings_df.iterrows():
        report_date = row["report_date"]
        quarter_begin = report_date.to_period("Q").start_time.normalize()
        quarter_return = nav_df.loc[
            (nav_df["trading_day"] >= quarter_begin) & (nav_df["trading_day"] <= report_date),
            "daily_return",
        ]
        if len(quarter_return) < 2:
            raise ValueError(f"{report_date:%Y-%m-%d} 季度有效日收益不足2条，无法计算波动率")
        annualized_vol = quarter_return.std(ddof=0) * np.sqrt(TRADING_DAYS_PER_YEAR)
        records.append(
            {
                "report_date": report_date.strftime("%Y-%m-%d"),
                "top10_weight_in_nv": float(row["top10_weight_in_nv"]),
                "annualized_volatility": float(annualized_vol),
            }
        )

    quarter_df = pd.DataFrame(records)
    if len(quarter_df) < 3:
        raise ValueError(f"对齐后的有效季度不足以计算相关性，当前仅 {len(quarter_df)} 个")
    return quarter_df


def build_output(quarter_df: pd.DataFrame) -> pd.DataFrame:
    if quarter_df["top10_weight_in_nv"].nunique() < 2:
        raise ValueError("前十大重仓股合计占比序列为常数，无法计算 Pearson 相关系数")
    if quarter_df["annualized_volatility"].nunique() < 2:
        raise ValueError("季度年化波动率序列为常数，无法计算 Pearson 相关系数")

    pearson = stats.pearsonr(
        quarter_df["top10_weight_in_nv"].to_numpy(),
        quarter_df["annualized_volatility"].to_numpy(),
    )

    result = pd.DataFrame(
        {
            "报告期": quarter_df["report_date"],
            "前十大重仓占净值比（%）": quarter_df["top10_weight_in_nv"].mul(100).map(lambda x: f"{x:.2f}"),
            "季度年化波动率（%）": quarter_df["annualized_volatility"].mul(100).map(lambda x: f"{x:.2f}"),
            "Pearson 相关系数": f"{float(pearson.statistic):.2f}",
            "p 值": f"{float(pearson.pvalue):.2f}",
        }
    )
    return result


def audit_return_coverage(holdings_df: pd.DataFrame, nav_df: pd.DataFrame) -> None:
    """Report incomplete coverage without inventing returns or changing the sample.

    The question defines market-day sampling but no minimum coverage ratio.
    This check must not silently drop quarters or fill missing returns.
    """
    start = holdings_df["report_date"].min().to_period("Q").start_time
    end = holdings_df["report_date"].max()
    sql = f"""
    SELECT DISTINCT DATE(TRADINGDATE) AS trading_day
    FROM qt_tradingdaynew
    WHERE SECUMARKET IN (83, 90)
      AND IFTRADINGDAY = 1
      AND TRADINGDATE >= '{start:%Y-%m-%d}'
      AND TRADINGDATE <= '{end:%Y-%m-%d}'
    ORDER BY trading_day
    """
    calendar = query_to_dataframe(sql, max_rows=10000, timeout=120)
    if calendar.empty:
        raise ValueError("交易日历为空，无法核验季度收益率覆盖")
    days = pd.DatetimeIndex(pd.to_datetime(calendar["trading_day"])).normalize().unique()
    available = pd.DatetimeIndex(nav_df.loc[
        np.isfinite(nav_df["daily_return"]), "trading_day"
    ]).normalize().unique()
    for report_date in holdings_df["report_date"]:
        expected = days[(days >= report_date.to_period("Q").start_time) & (days <= report_date)]
        if not len(expected):
            raise ValueError(f"{report_date:%Y-%m-%d} 所在季度交易日历为空")
        missing = expected.difference(available)
        if len(missing):
            warnings.warn(
                f"{report_date:%Y-%m-%d} 收益率覆盖：沪深交易日基准 "
                f"{len(expected) - len(missing)}/{len(expected)}，缺少 {len(missing)} 天；"
                "按可得日收益率计算，未补值，不代表完整季度覆盖。",
                RuntimeWarning,
                stacklevel=2,
            )


def main() -> pd.DataFrame:
    fund_innercode = get_fund_innercode()
    holdings_df = fetch_top10_weights(fund_innercode)
    start_date = holdings_df["report_date"].min().to_period("Q").start_time.normalize()
    end_date = holdings_df["report_date"].max().normalize()
    nav_df = fetch_nav(fund_innercode, start_date, end_date)
    audit_return_coverage(holdings_df, nav_df)
    quarter_df = build_quarter_metrics(holdings_df, nav_df)
    result = build_output(quarter_df)
    result.to_csv(SCRIPT_DIR / "result.csv", index=False, encoding="utf-8-sig")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
