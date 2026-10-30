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


AS_OF_DATE = "2026-01-05"
VISIBLE_CUTOFF = "2026-01-06 00:00:00"
INDEX_INNERCODE = 636661
OUTPUT_PATH = Path(__file__).with_name("result.csv")


def sql_int_list(values: list[int]) -> str:
    if not values:
        raise ValueError("empty integer list")
    return ",".join(str(int(v)) for v in values)


def load_lowest_fee_funds() -> pd.DataFrame:
    sql = f"""
    WITH params AS (
        SELECT DATE('{AS_OF_DATE}') AS as_of_date,
               TIMESTAMP('{VISIBLE_CUTOFF}') AS visible_cutoff
    ),
    candidate_funds AS (
        SELECT
            f.INNERCODE,
            sm.SECUCODE AS fund_code,
            sm.SECUABBR AS fund_name,
            DATE(f.ESTABLISHMENTDATE) AS establishment_date
        FROM mf_fundarchives f
        JOIN secumain sm
          ON sm.INNERCODE = f.INNERCODE
        JOIN params p
          ON 1 = 1
        WHERE sm.SECUCATEGORY = 8
          AND f.MAINCODE = f.SECURITYCODE
          AND f.ESTABLISHMENTDATE IS NOT NULL
          AND f.ESTABLISHMENTDATE <= p.as_of_date
          AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE > p.as_of_date)
          AND (f.INSERTTIME IS NULL OR f.INSERTTIME < p.visible_cutoff)
          AND f.FLOATTYPE IN (2, 3)
          AND COALESCE(f.TYPE, -1) NOT IN (4, 8)
          AND (COALESCE(sm.SECUABBR, '') LIKE '%A500%'
               OR COALESCE(sm.CHINAMEABBR, '') LIKE '%A500%')
          AND (COALESCE(sm.SECUABBR, '') LIKE '%指数增强%'
               OR COALESCE(sm.SECUABBR, '') LIKE '%指增%'
               OR COALESCE(sm.SECUABBR, '') LIKE '%指数量化增强%'
               OR COALESCE(sm.CHINAMEABBR, '') LIKE '%指数增强%'
               OR COALESCE(sm.CHINAMEABBR, '') LIKE '%指增%'
               OR COALESCE(sm.CHINAMEABBR, '') LIKE '%指数量化增强%')
          AND COALESCE(f.PERFORMANCEBENCHMARK, '') LIKE '%中证A500%'
    ),
    fee_ranked AS (
        SELECT
            cf.INNERCODE,
            c.CHARGERATETYPE,
            COALESCE(c.MAXCHARGERATE, c.MINCHARGERATE) AS fee_rate,
            ROW_NUMBER() OVER (
                PARTITION BY cf.INNERCODE, c.CHARGERATETYPE
                ORDER BY c.EXCUTEDATE DESC,
                         c.INFOPUBLDATE DESC,
                         c.ID DESC
            ) AS rn
        FROM candidate_funds cf
        JOIN mf_chargeratenew c
          ON c.INNERCODE = cf.INNERCODE
        JOIN params p
          ON 1 = 1
        WHERE c.CHARGERATETYPE IN (15000, 16000)
          AND c.CHARGERATEUNIT = 6
          AND (c.EXCUTEDATE IS NULL OR c.EXCUTEDATE <= p.as_of_date)
          AND (c.CANCELDATE IS NULL OR c.CANCELDATE > p.as_of_date)
          AND c.INFOPUBLDATE < p.visible_cutoff
    ),
    fee_current AS (
        SELECT
            INNERCODE,
            MAX(CASE WHEN CHARGERATETYPE = 15000 AND rn = 1 THEN fee_rate END) AS management_fee_rate,
            MAX(CASE WHEN CHARGERATETYPE = 16000 AND rn = 1 THEN fee_rate END) AS custody_fee_rate
        FROM fee_ranked
        GROUP BY INNERCODE
    ),
    fund_with_fee AS (
        SELECT
            cf.INNERCODE,
            cf.fund_code,
            cf.fund_name,
            cf.establishment_date,
            fc.management_fee_rate,
            fc.custody_fee_rate,
            fc.management_fee_rate + fc.custody_fee_rate AS total_fee_rate,
            DENSE_RANK() OVER (
                ORDER BY fc.management_fee_rate + fc.custody_fee_rate
            ) AS fee_rank
        FROM candidate_funds cf
        JOIN fee_current fc
          ON fc.INNERCODE = cf.INNERCODE
        WHERE fc.management_fee_rate IS NOT NULL
          AND fc.custody_fee_rate IS NOT NULL
    )
    SELECT
        INNERCODE,
        fund_code,
        fund_name,
        establishment_date,
        management_fee_rate,
        custody_fee_rate,
        total_fee_rate
    FROM fund_with_fee
    WHERE fee_rank = 1
    ORDER BY fund_code;
    """
    return query_to_dataframe(sql, max_rows=0, timeout=600)


def load_fund_nav(innercodes: list[int]) -> pd.DataFrame:
    sql = f"""
    SELECT
        x.INNERCODE,
        x.TRADINGDAY,
        x.UNITNVRESTORED
    FROM (
        SELECT
            n.INNERCODE,
            DATE(n.TRADINGDAY) AS TRADINGDAY,
            n.UNITNVRESTORED,
            ROW_NUMBER() OVER (
                PARTITION BY n.INNERCODE, DATE(n.TRADINGDAY)
                ORDER BY n.INSERTTIME DESC,
                         n.UPDATETIME DESC,
                         n.ID DESC
            ) AS rn
        FROM mf_fundnetvalueretrans n
        WHERE n.INNERCODE IN ({sql_int_list(innercodes)})
          AND n.TRADINGDAY <= DATE('{AS_OF_DATE}')
          AND n.INSERTTIME < TIMESTAMP('{VISIBLE_CUTOFF}')
    ) x
    WHERE x.rn = 1
    ORDER BY x.INNERCODE, x.TRADINGDAY;
    """
    return query_to_dataframe(sql, max_rows=0, timeout=600)


def load_index_quote() -> pd.DataFrame:
    sql = f"""
    SELECT
        DATE(q.TRADINGDAY) AS TRADINGDAY,
        q.CLOSEPRICE
    FROM qt_indexquote q
    WHERE q.INNERCODE = {INDEX_INNERCODE}
      AND q.CLOSEPRICE IS NOT NULL
      AND q.TRADINGDAY <= DATE('{AS_OF_DATE}')
    ORDER BY q.TRADINGDAY;
    """
    return query_to_dataframe(sql, max_rows=0, timeout=600)


def load_trading_calendar(start_date: str) -> pd.DataFrame:
    return query_to_dataframe(f"""
        SELECT DISTINCT DATE(TRADINGDATE) AS TRADINGDAY
        FROM qt_tradingdaynew
        WHERE IFTRADINGDAY = 1 AND SECUMARKET IN (83, 90)
          AND TRADINGDATE BETWEEN DATE('{start_date}') AND DATE('{AS_OF_DATE}')
        ORDER BY TRADINGDAY
    """, max_rows=0, timeout=120)


def calculate_metrics(funds: pd.DataFrame, nav: pd.DataFrame, index_quote: pd.DataFrame,
                      trading_calendar: pd.DataFrame) -> pd.DataFrame:
    columns = ["INNERCODE", "excess_return_pct", "tracking_error_pct", "effective_sample_days"]
    if funds.empty:
        return pd.DataFrame(columns=columns)
    calendar = pd.DatetimeIndex(pd.to_datetime(trading_calendar["TRADINGDAY"]).dt.normalize()).unique().sort_values()
    if calendar.empty:
        raise ValueError("No A-share trading calendar for tracking-error window")
    nav = nav.reindex(columns=["INNERCODE", "TRADINGDAY", "UNITNVRESTORED"]) if nav.empty else nav.copy()
    index_quote = index_quote.reindex(columns=["TRADINGDAY", "CLOSEPRICE"]) if index_quote.empty else index_quote.copy()
    for frame in (nav, index_quote):
        frame["TRADINGDAY"] = pd.to_datetime(frame["TRADINGDAY"]).dt.normalize()
    if nav.duplicated(["INNERCODE", "TRADINGDAY"]).any() or index_quote.duplicated("TRADINGDAY").any():
        raise ValueError("Daily price versions must be unique before return calculation")
    rows = []
    for fund in funds.itertuples(index=False):
        start = pd.Timestamp(fund.establishment_date).normalize()
        dates = calendar[(calendar >= start) & (calendar <= pd.Timestamp(AS_OF_DATE))]
        prices = pd.DataFrame(index=dates)
        fn = nav[nav["INNERCODE"] == fund.INNERCODE].set_index("TRADINGDAY")["UNITNVRESTORED"]
        ix = index_quote.set_index("TRADINGDAY")["CLOSEPRICE"]
        prices["fund"] = pd.to_numeric(fn, errors="coerce").reindex(dates)
        prices["index"] = pd.to_numeric(ix, errors="coerce").reindex(dates)
        prices = prices.where(np.isfinite(prices) & (prices > 0))
        common = prices.dropna()
        excess = np.nan
        if len(common) >= 2:
            interval = common.iloc[-1] / common.iloc[0] - 1
            excess = (interval["fund"] - interval["index"]) * 100
        daily = prices.pct_change(fill_method=None)
        active = (daily["fund"] - daily["index"]).replace([np.inf, -np.inf], np.nan).dropna()
        rows.append({"INNERCODE": fund.INNERCODE, "excess_return_pct": excess,
                     "tracking_error_pct": active.std(ddof=0) * np.sqrt(252) * 100 if len(active) >= 2 else np.nan,
                     "effective_sample_days": int(len(active))})
    return pd.DataFrame(rows, columns=columns)


def main() -> pd.DataFrame:
    funds = load_lowest_fee_funds()
    if funds.empty:
        result = pd.DataFrame(columns=[
            "基金代码", "简称", "管理费率", "托管费率", "费率合计",
            "超额收益", "跟踪误差", "有效样本天数",
        ])
    else:
        innercodes = [int(x) for x in funds["INNERCODE"].dropna().tolist()]
        start = pd.to_datetime(funds["establishment_date"]).min().strftime("%Y-%m-%d")
        metrics = calculate_metrics(funds, load_fund_nav(innercodes), load_index_quote(),
                                    load_trading_calendar(start))
        result = funds.merge(metrics, on="INNERCODE", how="left")
        result = result.sort_values("fund_code")
        result = result.rename(columns={
            "fund_code": "基金代码",
            "fund_name": "简称",
            "management_fee_rate": "管理费率",
            "custody_fee_rate": "托管费率",
            "total_fee_rate": "费率合计",
            "excess_return_pct": "超额收益",
            "tracking_error_pct": "跟踪误差",
            "effective_sample_days": "有效样本天数",
        })
        result = result[[
            "基金代码", "简称", "管理费率", "托管费率", "费率合计",
            "超额收益", "跟踪误差", "有效样本天数",
        ]]

    result.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
