#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = "2024-01-05"
VISIBLE_CUTOFF = "2024-01-06 00:00:00"
START_DATE = "2023-01-01"
END_DATE = "2023-12-31"
MIN_EFFECTIVE_DAYS = 200
MIN_NEGATIVE_DAYS = 2
ANNUAL_TRADING_DAYS = 252
OUTPUT_PATH = Path(__file__).with_name("result.csv")
ABSTRACT_PATH = Path(__file__).with_name("abstract.txt")


def load_fund_stats() -> pd.DataFrame:
    sql = f"""
    WITH params AS (
        SELECT DATE('{START_DATE}') AS start_date,
               DATE('{END_DATE}') AS end_date,
               TIMESTAMP('{VISIBLE_CUTOFF}') AS visible_cutoff
    ),
    type_ranked AS (
        SELECT
            t.INNERCODE,
            ROW_NUMBER() OVER (
                PARTITION BY t.INNERCODE
                ORDER BY t.EFFECTIVEDATE DESC,
                         t.INSERTTIME DESC,
                         t.ID DESC
            ) AS rn
        FROM mf_jyfundtype t
        JOIN params p
          ON 1 = 1
        WHERE t.STANDARD = 75
          AND t.SECASSETCATCODE = 1101
          AND t.EFFECTIVEDATE <= p.end_date
          AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.end_date)
          AND t.INSERTTIME < p.visible_cutoff
          AND t.UPDATETIME < p.visible_cutoff
    ),
    fund_pool AS (
        SELECT
            fa.INNERCODE,
            sm.SECUCODE AS fund_code,
            sm.SECUABBR AS fund_name
        FROM mf_fundarchives fa
        JOIN type_ranked tr
          ON tr.INNERCODE = fa.INNERCODE
         AND tr.rn = 1
        JOIN secumain sm
          ON sm.INNERCODE = fa.INNERCODE
        JOIN params p
          ON 1 = 1
        WHERE sm.SECUCATEGORY = 8
          AND fa.TYPE = 2
          AND fa.FLOATTYPE = 2
          AND fa.MAINCODE = fa.SECURITYCODE
          AND fa.ESTABLISHMENTDATE <= p.end_date
          AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > p.end_date)
          AND (fa.INSERTTIME IS NULL OR fa.INSERTTIME < p.visible_cutoff)
    ),
    nav_daily AS (
        SELECT
            fp.INNERCODE,
            fp.fund_code,
            fp.fund_name,
            DATE(n.TRADINGDAY) AS trading_day,
            n.NVRDAILYGROWTHRATE / 100.0 AS daily_return
        FROM fund_pool fp
        JOIN mf_fundnetvalueretrans n
          ON n.INNERCODE = fp.INNERCODE
        JOIN params p
          ON 1 = 1
        WHERE n.TRADINGDAY >= p.start_date
          AND n.TRADINGDAY <= p.end_date
          AND n.NVRDAILYGROWTHRATE IS NOT NULL
          AND n.INSERTTIME < p.visible_cutoff
          AND n.UPDATETIME < p.visible_cutoff
    ),
    fund_stats AS (
        SELECT
            INNERCODE,
            fund_code,
            fund_name,
            COUNT(*) AS effective_days,
            SUM(CASE WHEN daily_return < 0 THEN 1 ELSE 0 END) AS negative_days,
            STDDEV_POP(CASE WHEN daily_return < 0 THEN daily_return END)
                * SQRT({ANNUAL_TRADING_DAYS}) * 100 AS downside_vol_pct
        FROM nav_daily
        GROUP BY INNERCODE, fund_code, fund_name
    )
    SELECT
        fund_code,
        fund_name,
        effective_days,
        negative_days,
        downside_vol_pct
    FROM fund_stats
    ORDER BY fund_code;
    """
    return query_to_dataframe(sql, max_rows=0, timeout=600)


def build_result(stats: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    if stats.empty:
        empty = pd.DataFrame(columns=["基金代码", "基金简称", "年化下行波动率"])
        return empty, {
            "有净值日收益基金数": 0,
            "有效日收益不足剔除数": 0,
            "负收益日不足剔除数": 0,
            "入选计算基金数": 0,
        }

    stats = stats.copy()
    stats["effective_days"] = pd.to_numeric(stats["effective_days"], errors="coerce")
    stats["negative_days"] = pd.to_numeric(stats["negative_days"], errors="coerce")
    stats["downside_vol_pct"] = pd.to_numeric(stats["downside_vol_pct"], errors="coerce")

    enough_days = stats["effective_days"] >= MIN_EFFECTIVE_DAYS
    enough_negative = stats["negative_days"] >= MIN_NEGATIVE_DAYS
    eligible = stats[enough_days & enough_negative].dropna(subset=["downside_vol_pct"]).copy()

    summary = {
        "有净值日收益基金数": int(len(stats)),
        "有效日收益不足剔除数": int((~enough_days).sum()),
        "负收益日不足剔除数": int((enough_days & ~enough_negative).sum()),
        "入选计算基金数": int(len(eligible)),
    }

    result = (
        eligible.sort_values(["downside_vol_pct", "fund_code"], ascending=[True, True])
        .head(10)
        .assign(downside_vol_pct=lambda x: x["downside_vol_pct"].round(2))
        .rename(
            columns={
                "fund_code": "基金代码",
                "fund_name": "基金简称",
                "downside_vol_pct": "年化下行波动率",
            }
        )
    )
    return result[["基金代码", "基金简称", "年化下行波动率"]], summary


def main() -> pd.DataFrame:
    stats = load_fund_stats()
    result, summary = build_result(stats)

    result.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig", float_format="%.2f")
    ABSTRACT_PATH.write_text(
        "样本复核摘要\n"
        f"有净值日收益基金数：{summary['有净值日收益基金数']}。"
        f"有效日收益不足{MIN_EFFECTIVE_DAYS}日剔除数：{summary['有效日收益不足剔除数']}。"
        f"负收益日不足{MIN_NEGATIVE_DAYS}日剔除数：{summary['负收益日不足剔除数']}。"
        f"入选计算基金数：{summary['入选计算基金数']}。\n",
        encoding="utf-8",
    )
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
