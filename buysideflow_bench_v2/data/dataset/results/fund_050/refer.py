#!/usr/bin/env python3
from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

import pandas as pd
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe

BASE_DIR = Path(__file__).resolve().parent
TARGET_DATE = "2025-04-07"
EVENT_CUTOFF = "2025-04-08"
TRADING_DAY_LAG = 5
UNDERPERFORM_THRESHOLD = -0.05
RANK_PCT_CHANGE_THRESHOLD = 0.30

EXPECTED_COLUMNS = [
    "基金代码",
    "基金简称",
    "聚源二级分类",
    "基金当日收益(%)",
    "相对沪深300收益(%)",
    "参照日排名分位(%)",
    "目标日排名分位(%)",
    "参照日同类样本数",
    "目标日同类样本数",
    "排名分位变化(百分点)",
]


def scalar(sql: str):
    df = query_to_dataframe(sql, max_rows=0, timeout=120)
    if df is None or df.empty:
        raise ValueError(f"Query returned no rows: {sql[:120]}")
    return df.iloc[0, 0]


def get_prev_trading_date() -> str:
    offset = TRADING_DAY_LAG - 1
    sql = f"""
    SELECT DATE_FORMAT(trade_date, '%Y-%m-%d') AS prev_date
    FROM (
        SELECT DATE(TRADINGDATE) AS trade_date
        FROM qt_tradingdaynew
        WHERE IFTRADINGDAY = 1
          AND SECUMARKET IN (83, 90)
          AND TRADINGDATE < DATE('{TARGET_DATE}')
          AND TRADINGDATE >= DATE_SUB(DATE('{TARGET_DATE}'), INTERVAL 1 MONTH)
        GROUP BY DATE(TRADINGDATE)
    ) d
    ORDER BY trade_date DESC
    LIMIT 1 OFFSET {offset}
    """
    return str(scalar(sql))


def get_hs300_return() -> float:
    sql = f"""
    SELECT q.CLOSEPRICE / NULLIF(q.PREVCLOSEPRICE, 0) - 1 AS hs300_ret
    FROM qt_indexquote q
    JOIN secumain s
      ON s.INNERCODE = q.INNERCODE
     AND s.SECUCATEGORY = 4
     AND s.SECUCODE = '000300'
    WHERE q.TRADINGDAY = DATE('{TARGET_DATE}')
      AND q.CLOSEPRICE > 0
      AND q.PREVCLOSEPRICE > 0
    ORDER BY q.XGRQ DESC, q.ID DESC
    LIMIT 1
    """
    return float(scalar(sql))


def load_returns(prev_date: str) -> pd.DataFrame:
    sql = f"""
    WITH nav_ranked AS (
        SELECT
            n.INNERCODE,
            DATE(n.TRADINGDAY) AS trade_date,
            n.NVRDAILYGROWTHRATE / 100 AS fund_ret,
            ROW_NUMBER() OVER (
                PARTITION BY n.INNERCODE, n.TRADINGDAY
                ORDER BY n.INSERTTIME DESC, n.UPDATETIME DESC, n.ID DESC
            ) AS rn
        FROM mf_fundnetvalueretrans n
        WHERE n.TRADINGDAY IN (DATE('{prev_date}'), DATE('{TARGET_DATE}'))
          AND n.INSERTTIME < DATE('{EVENT_CUTOFF}')
    )
    SELECT
        DATE_FORMAT(n.trade_date, '%Y-%m-%d') AS trade_date,
        fa.INNERCODE AS fund_innercode,
        fa.SECURITYCODE AS fund_code,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, fa.SECURITYCODE) AS fund_name,
        n.fund_ret
    FROM nav_ranked n
    JOIN mf_fundarchives fa
      ON fa.INNERCODE = n.INNERCODE
     AND fa.MAINCODE = fa.SECURITYCODE
     AND fa.INVESTMENTTYPE = 16
     AND COALESCE(fa.IFFOF, 2) <> 1
     AND fa.ESTABLISHMENTDATE <= n.trade_date
     AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= n.trade_date)
     AND (fa.INSERTTIME IS NULL OR fa.INSERTTIME < DATE('{EVENT_CUTOFF}'))
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    WHERE n.rn = 1
      AND n.fund_ret IS NOT NULL
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=300)
    if df is None or df.empty:
        raise ValueError("No fund return rows for target and reference dates")
    return df


def load_classes(innercodes: pd.Series, prev_date: str) -> pd.DataFrame:
    codes = ",".join(str(int(code)) for code in sorted(pd.unique(innercodes.dropna())))
    if not codes:
        raise ValueError("No fund innercodes available for class lookup")
    sql = f"""
    SELECT
        INNERCODE AS fund_innercode,
        SECASSETCATCODE AS sec_cat_code,
        SECASSETCATNAME AS sec_cat_name,
        EFFECTIVEDATE,
        CANCELDATE,
        INSERTTIME AS class_insert_time,
        ID AS class_id
    FROM mf_jyfundtype
    WHERE INNERCODE IN ({codes})
      AND STANDARD = 75
      AND EFFECTIVEDATE <= DATE('{TARGET_DATE}')
      AND (CANCELDATE IS NULL OR CANCELDATE > DATE('{prev_date}'))
      AND INSERTTIME < DATE('{EVENT_CUTOFF}')
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=300)
    if df is None or df.empty:
        raise ValueError("No available fund classification rows")
    return df


def classify_and_rank(returns: pd.DataFrame, classes: pd.DataFrame) -> pd.DataFrame:
    panel = returns.merge(classes, on="fund_innercode", how="inner")
    panel["fund_ret"] = pd.to_numeric(panel["fund_ret"], errors="coerce")
    panel["sec_cat_code"] = pd.to_numeric(panel["sec_cat_code"], errors="coerce")
    panel["EFFECTIVEDATE"] = pd.to_datetime(panel["EFFECTIVEDATE"], errors="coerce")
    panel["CANCELDATE"] = pd.to_datetime(panel["CANCELDATE"], errors="coerce")
    panel["trade_date_dt"] = pd.to_datetime(panel["trade_date"], errors="coerce")
    panel["class_insert_time"] = pd.to_datetime(panel["class_insert_time"], errors="coerce")
    panel["class_id"] = pd.to_numeric(panel["class_id"], errors="coerce")
    panel = panel[
        (panel["EFFECTIVEDATE"] <= panel["trade_date_dt"])
        & (panel["CANCELDATE"].isna() | (panel["CANCELDATE"] > panel["trade_date_dt"]))
    ].copy()

    panel = panel.sort_values(
        ["trade_date", "fund_innercode", "EFFECTIVEDATE", "class_insert_time", "class_id"],
        ascending=[True, True, False, False, False],
    ).drop_duplicates(["trade_date", "fund_innercode"], keep="first")

    panel = panel[
        panel["sec_cat_code"].between(1101, 1199)
        & panel["sec_cat_code"].ne(1102)
        & np.isfinite(panel["fund_ret"])
    ].copy()
    panel = panel.sort_values(
        ["trade_date", "sec_cat_code", "fund_ret", "fund_code"],
        ascending=[True, True, False, True],
    )
    group_cols = ["trade_date", "sec_cat_code"]
    panel["rank_num"] = panel.groupby(group_cols)["fund_ret"].rank(method="min", ascending=False).astype(int)
    panel["sample_size"] = panel.groupby(group_cols)["fund_innercode"].transform("size")
    panel["rank_pct"] = (panel["rank_num"] - 1) / (panel["sample_size"] - 1).clip(lower=1)
    return panel


def build_result(panel: pd.DataFrame, prev_date: str, hs300_ret: float) -> pd.DataFrame:
    current = panel[panel["trade_date"] == TARGET_DATE].copy()
    previous = panel[panel["trade_date"] == prev_date].copy()
    merged = current.merge(
        previous[["fund_innercode", "sec_cat_code", "rank_pct", "rank_num", "sample_size"]],
        on=["fund_innercode", "sec_cat_code"],
        how="inner",
        suffixes=("", "_prev"),
    )
    relative = merged["fund_ret"].map(lambda value: Fraction(str(value)) - Fraction(str(hs300_ret)))
    # Compare rational ranks exactly at the strict 30-percentage-point boundary.
    rank_change = pd.Series([
        Fraction(int(rank) - 1, max(int(size) - 1, 1))
        - Fraction(int(prior_rank) - 1, max(int(prior_size) - 1, 1))
        for rank, size, prior_rank, prior_size in zip(
            merged["rank_num"], merged["sample_size"],
            merged["rank_num_prev"], merged["sample_size_prev"])
    ], index=merged.index, dtype=object)
    merged["relative_to_hs300"] = relative.map(float)
    merged["rank_pct_change"] = rank_change.map(float)
    filtered = merged[
        (relative <= Fraction(str(UNDERPERFORM_THRESHOLD)))
        & (rank_change > Fraction(str(RANK_PCT_CHANGE_THRESHOLD)))
    ].copy()
    filtered = filtered.sort_values("fund_code")

    result = pd.DataFrame(
        {
            "基金代码": filtered["fund_code"].astype(str) + ".OF",
            "基金简称": filtered["fund_name"],
            "聚源二级分类": filtered["sec_cat_name"],
            "基金当日收益(%)": filtered["fund_ret"] * 100,
            "相对沪深300收益(%)": filtered["relative_to_hs300"] * 100,
            "参照日排名分位(%)": filtered["rank_pct_prev"] * 100,
            "目标日排名分位(%)": filtered["rank_pct"] * 100,
            "参照日同类样本数": filtered["sample_size_prev"].astype("Int64"),
            "目标日同类样本数": filtered["sample_size"].astype("Int64"),
            "排名分位变化(百分点)": filtered["rank_pct_change"] * 100,
        }
    )
    numeric_cols = [
        "基金当日收益(%)",
        "相对沪深300收益(%)",
        "参照日排名分位(%)",
        "目标日排名分位(%)",
        "排名分位变化(百分点)",
    ]
    result[numeric_cols] = result[numeric_cols].round(2)
    return result[EXPECTED_COLUMNS].reset_index(drop=True)


def main() -> pd.DataFrame:
    prev_date = get_prev_trading_date()
    hs300_ret = get_hs300_return()
    returns = load_returns(prev_date)
    classes = load_classes(returns["fund_innercode"], prev_date)
    panel = classify_and_rank(returns, classes)
    result = build_result(panel, prev_date, hs300_ret)
    if "sql_code" not in globals():
        result.to_csv(BASE_DIR / "result.csv", index=False, encoding="utf-8-sig", float_format="%.2f")
    return result


if __name__ == "__main__":
    result = main()
    print(result)
