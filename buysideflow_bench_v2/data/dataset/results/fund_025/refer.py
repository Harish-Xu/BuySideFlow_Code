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


AS_OF_DATE = pd.Timestamp("2025-04-08")
AS_OF_NEXT_DAY = AS_OF_DATE + pd.Timedelta(days=1)
TTM_START = AS_OF_DATE - pd.DateOffset(years=1) + pd.Timedelta(days=1)
TARGET_FUND_CODE = "001076"


def _format_ids(values: pd.Series) -> str:
    ids = sorted({int(v) for v in values.dropna().tolist()})
    if not ids:
        raise ValueError("股票内部编码为空")
    return ",".join(str(v) for v in ids)


TOP10_SQL = f"""
WITH params AS (
    SELECT
        DATE('{AS_OF_DATE:%Y-%m-%d}') AS as_of_date,
        DATE('{AS_OF_NEXT_DAY:%Y-%m-%d}') AS as_of_next_day
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
    ORDER BY
        CASE WHEN COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE THEN 0 ELSE 1 END,
        sm.INNERCODE
    LIMIT 1
),
latest_report AS (
    SELECT MAX(DATE(k.REPORTDATE)) AS report_date
    FROM mf_keystockportfolio k
    JOIN target_fund tf
      ON tf.INNERCODE = k.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE k.SERIALNUMBER BETWEEN 1 AND 10
      AND k.STOCKINNERCODE IS NOT NULL
      AND k.RATIOINNV IS NOT NULL
      AND (k.INFOPUBLDATE IS NULL OR DATE(k.INFOPUBLDATE) < p.as_of_next_day)
      AND (k.INSERTTIME IS NULL OR k.INSERTTIME < p.as_of_next_day)
),
holding_ranked AS (
    SELECT
        tf.fund_code,
        tf.fund_name,
        lr.report_date,
        k.SERIALNUMBER AS serial_number,
        k.STOCKINNERCODE AS stock_innercode,
        k.RATIOINNV AS holding_weight,
        ROW_NUMBER() OVER (
            PARTITION BY k.INNERCODE, DATE(k.REPORTDATE), k.SERIALNUMBER
            ORDER BY
                COALESCE(k.INFOPUBLDATE, DATE('1900-01-01')) DESC,
                COALESCE(k.INSERTTIME, DATE('1900-01-01')) DESC,
                k.XGRQ DESC,
                k.ID DESC
        ) AS rn
    FROM mf_keystockportfolio k
    JOIN target_fund tf
      ON tf.INNERCODE = k.INNERCODE
    JOIN latest_report lr
      ON DATE(k.REPORTDATE) = lr.report_date
    JOIN params p
      ON 1 = 1
    WHERE k.SERIALNUMBER BETWEEN 1 AND 10
      AND k.STOCKINNERCODE IS NOT NULL
      AND k.RATIOINNV IS NOT NULL
      AND k.RATIOINNV > 0
      AND (k.INFOPUBLDATE IS NULL OR DATE(k.INFOPUBLDATE) < p.as_of_next_day)
      AND (k.INSERTTIME IS NULL OR k.INSERTTIME < p.as_of_next_day)
),
stock_map AS (
    SELECT
        INNERCODE AS stock_innercode,
        SECUCODE AS stock_code,
        COALESCE(CHINAMEABBR, SECUABBR, CHINAME) AS stock_name,
        0 AS is_hk
    FROM secumain
    UNION ALL
    SELECT
        INNERCODE AS stock_innercode,
        SECUCODE AS stock_code,
        COALESCE(CHINAMEABBR, SECUABBR, CHINAME) AS stock_name,
        1 AS is_hk
    FROM hk_secumain
)
SELECT
    h.fund_code,
    h.fund_name,
    h.report_date,
    h.serial_number,
    h.stock_innercode,
    sm.stock_code,
    sm.stock_name,
    sm.is_hk,
    h.holding_weight
FROM holding_ranked h
LEFT JOIN stock_map sm
  ON sm.stock_innercode = h.stock_innercode
WHERE h.rn = 1
ORDER BY h.serial_number
"""


def build_price_sql(stock_ids: str) -> str:
    return f"""
    WITH price_union AS (
        SELECT
            q.INNERCODE AS stock_innercode,
            DATE(q.TRADINGDAY) AS price_date,
            q.CLOSEPRICE AS close_price,
            1 AS source_priority,
            q.ID AS row_id
        FROM qt_dailyquote q
        WHERE q.INNERCODE IN ({stock_ids})
          AND q.TRADINGDAY <= DATE('{AS_OF_DATE:%Y-%m-%d}')
          AND q.CLOSEPRICE IS NOT NULL
          AND q.CLOSEPRICE > 0
          AND q.XGRQ < DATE('{AS_OF_NEXT_DAY:%Y-%m-%d}')

        UNION ALL

        SELECT
            q.INNERCODE AS stock_innercode,
            DATE(q.TRADINGDAY) AS price_date,
            q.CLOSEPRICE AS close_price,
            0 AS source_priority,
            q.ID AS row_id
        FROM lc_stibdailyquote q
        WHERE q.INNERCODE IN ({stock_ids})
          AND q.TRADINGDAY <= DATE('{AS_OF_DATE:%Y-%m-%d}')
          AND q.CLOSEPRICE IS NOT NULL
          AND q.CLOSEPRICE > 0
          AND q.INSERTTIME < DATE('{AS_OF_NEXT_DAY:%Y-%m-%d}')
          AND q.UPDATETIME < DATE('{AS_OF_NEXT_DAY:%Y-%m-%d}')

        UNION ALL

        SELECT
            q.INNERCODE AS stock_innercode,
            DATE(q.TRADINGDAY) AS price_date,
            q.CLOSEPRICE AS close_price,
            1 AS source_priority,
            q.ID AS row_id
        FROM qt_hkdailyquote q
        WHERE q.INNERCODE IN ({stock_ids})
          AND q.TRADINGDAY <= DATE('{AS_OF_DATE:%Y-%m-%d}')
          AND q.CLOSEPRICE IS NOT NULL
          AND q.CLOSEPRICE > 0
          AND q.XGRQ < DATE('{AS_OF_NEXT_DAY:%Y-%m-%d}')
    ),
    price_ranked AS (
        SELECT
            stock_innercode,
            price_date,
            close_price,
            ROW_NUMBER() OVER (
                PARTITION BY stock_innercode
                ORDER BY price_date DESC, source_priority ASC, row_id DESC
            ) AS rn
        FROM price_union
    )
    SELECT stock_innercode, price_date, close_price
    FROM price_ranked
    WHERE rn = 1
    """


def build_dividend_sql(stock_ids: str) -> str:
    return f"""
    WITH dividend_events AS (
        SELECT
            d.INNERCODE AS stock_innercode,
            YEAR(d.ENDDATE) AS dividend_year,
            COALESCE(d.EXDIVIDATE, d.TOACCOUNTDATE, d.DIVIDENDIMPLEMENTDATE, d.DIVIENDDATE, d.ENDDATE) AS event_date,
            COALESCE(d.LATESTINFOPUBLDATE, d.DIVIDENDIMPLEMENTDATE, d.DIVIINTENTPUBLDATE, d.EXDIVIDATE, d.TOACCOUNTDATE, d.DIVIENDDATE, d.ENDDATE) AS visible_date,
            d.EVENTPROCEDURE AS event_procedure,
            CASE
                WHEN d.IFDIVIDEND = 1 THEN COALESCE(d.CASHDIVIRMBADJUSTED, d.ACTUALCASHDIVIRMB, d.CASHDIVIRMB, 0) / 10.0
                ELSE 0
            END AS cash_div_per_share,
            d.ID AS row_id
        FROM lc_dividend d
        WHERE d.INNERCODE IN ({stock_ids})
          AND COALESCE(d.LATESTINFOPUBLDATE, d.DIVIDENDIMPLEMENTDATE, d.DIVIINTENTPUBLDATE, d.EXDIVIDATE, d.TOACCOUNTDATE, d.DIVIENDDATE, d.ENDDATE) < DATE('{AS_OF_NEXT_DAY:%Y-%m-%d}')

        UNION ALL

        SELECT
            d.INNERCODE AS stock_innercode,
            YEAR(d.ENDDATE) AS dividend_year,
            COALESCE(d.EXDIVIDATE, d.TOACCOUNTDATE, d.INFOPUBLDATE, d.ENDDATE) AS event_date,
            d.INFOPUBLDATE AS visible_date,
            d.EVENTPROCEDURE AS event_procedure,
            CASE
                WHEN d.IFDIVIDEND = 1 THEN COALESCE(d.CASHDIVIRMBADJUSTED, d.ACTUALCASHDIVIRMB, d.CASHDIVIRMB, 0) / 10.0
                ELSE 0
            END AS cash_div_per_share,
            d.ID AS row_id
        FROM lc_stibdividend d
        WHERE d.INNERCODE IN ({stock_ids})
          AND d.INFOPUBLDATE < DATE('{AS_OF_NEXT_DAY:%Y-%m-%d}')
          AND d.INSERTTIME < DATE('{AS_OF_NEXT_DAY:%Y-%m-%d}')
          AND d.UPDATETIME < DATE('{AS_OF_NEXT_DAY:%Y-%m-%d}')
    ),
    dividend_ranked AS (
        SELECT
            stock_innercode,
            dividend_year,
            DATE(event_date) AS event_date,
            DATE(visible_date) AS visible_date,
            event_procedure,
            cash_div_per_share,
            ROW_NUMBER() OVER (
                PARTITION BY stock_innercode, dividend_year, DATE(event_date), cash_div_per_share
                ORDER BY visible_date DESC, row_id DESC
            ) AS rn
        FROM dividend_events
        WHERE visible_date IS NOT NULL
    )
    SELECT
        stock_innercode,
        dividend_year,
        event_date,
        visible_date,
        event_procedure,
        cash_div_per_share
    FROM dividend_ranked
    WHERE rn = 1
    """


def main() -> pd.DataFrame:
    top10 = query_to_dataframe(TOP10_SQL, max_rows=100, timeout=300)
    if top10.empty:
        raise ValueError("未找到观察日前可见的前十大重仓股")

    top10["stock_innercode"] = pd.to_numeric(top10["stock_innercode"], errors="coerce")
    top10["holding_weight"] = pd.to_numeric(top10["holding_weight"], errors="coerce")
    top10["report_date"] = pd.to_datetime(top10["report_date"], errors="coerce")
    top10 = top10.dropna(subset=["stock_innercode", "holding_weight", "report_date"]).copy()
    top10["stock_innercode"] = top10["stock_innercode"].astype(int)

    if len(top10) != 10:
        raise ValueError(f"前十大重仓股数量不是 10：{len(top10)}")
    if top10["holding_weight"].le(0).any():
        raise ValueError("存在非正持仓权重")

    stock_ids = _format_ids(top10["stock_innercode"])
    prices = query_to_dataframe(build_price_sql(stock_ids), max_rows=100, timeout=300)
    dividends = query_to_dataframe(build_dividend_sql(stock_ids), max_rows=1000, timeout=300)

    prices["stock_innercode"] = pd.to_numeric(prices["stock_innercode"], errors="coerce")
    prices["close_price"] = pd.to_numeric(prices["close_price"], errors="coerce")
    prices["price_date"] = pd.to_datetime(prices["price_date"], errors="coerce")
    prices = prices.dropna(subset=["stock_innercode", "close_price", "price_date"]).copy()
    prices["stock_innercode"] = prices["stock_innercode"].astype(int)

    if not dividends.empty:
        dividends["stock_innercode"] = pd.to_numeric(dividends["stock_innercode"], errors="coerce")
        dividends["dividend_year"] = pd.to_numeric(dividends["dividend_year"], errors="coerce")
        dividends["cash_div_per_share"] = pd.to_numeric(dividends["cash_div_per_share"], errors="coerce")
        dividends["event_procedure"] = pd.to_numeric(dividends["event_procedure"], errors="coerce")
        dividends["event_date"] = pd.to_datetime(dividends["event_date"], errors="coerce")
        dividends["visible_date"] = pd.to_datetime(dividends["visible_date"], errors="coerce")
        dividends = dividends.dropna(
            subset=["stock_innercode", "dividend_year", "cash_div_per_share", "visible_date"]
        ).copy()
        dividends["stock_innercode"] = dividends["stock_innercode"].astype(int)
        dividends["dividend_year"] = dividends["dividend_year"].astype(int)
    else:
        dividends = pd.DataFrame(
            columns=[
                "stock_innercode",
                "dividend_year",
                "event_date",
                "visible_date",
                "event_procedure",
                "cash_div_per_share",
            ]
        )

    panel = top10.merge(prices, on="stock_innercode", how="left")
    missing_price = panel[panel["close_price"].isna()]
    if not missing_price.empty:
        missing_codes = ",".join(missing_price["stock_code"].fillna("UNKNOWN").astype(str).tolist())
        raise ValueError(f"前十大重仓股缺少观察日可见价格：{missing_codes}")

    implemented_ttm = dividends[
        (dividends["event_procedure"] == 3131)
        & (dividends["event_date"] >= TTM_START)
        & (dividends["event_date"] <= AS_OF_DATE)
    ].copy()
    ttm_div = (
        implemented_ttm.groupby("stock_innercode", as_index=False)["cash_div_per_share"]
        .sum()
        .rename(columns={"cash_div_per_share": "ttm_div_per_share"})
    )

    year_dividend = (
        dividends.groupby(["stock_innercode", "dividend_year"], as_index=False)["cash_div_per_share"]
        .max()
        .copy()
    )
    latest_year = year_dividend.groupby("stock_innercode", as_index=False)["dividend_year"].max()

    def has_continuous_three_years(stock_innercode: int, max_year: float) -> int:
        if pd.isna(max_year):
            return 0
        year_cash = dict(
            zip(
                year_dividend.loc[year_dividend["stock_innercode"] == stock_innercode, "dividend_year"].astype(int),
                year_dividend.loc[year_dividend["stock_innercode"] == stock_innercode, "cash_div_per_share"],
            )
        )
        max_year = int(max_year)
        required = {max_year, max_year - 1, max_year - 2}
        return int(all(year_cash.get(year, 0) > 0 for year in required))

    latest_year["cont_dividend_3y"] = latest_year.apply(
        lambda row: has_continuous_three_years(int(row["stock_innercode"]), row["dividend_year"]),
        axis=1,
    )

    panel = panel.merge(ttm_div, on="stock_innercode", how="left")
    panel = panel.merge(latest_year[["stock_innercode", "cont_dividend_3y"]], on="stock_innercode", how="left")
    panel["ttm_div_per_share"] = panel["ttm_div_per_share"].fillna(0.0)
    panel["cont_dividend_3y"] = panel["cont_dividend_3y"].fillna(0).astype(int)
    panel["dividend_yield_12m"] = panel["ttm_div_per_share"] / panel["close_price"]

    top10_weight = panel["holding_weight"].sum()
    weighted_dividend_yield = (panel["holding_weight"] * panel["dividend_yield_12m"]).sum() / top10_weight
    continuous_weight_ratio = (
        panel.loc[panel["cont_dividend_3y"] == 1, "holding_weight"].sum() / top10_weight
    )

    first = panel.iloc[0]
    result = pd.DataFrame(
        [
            {
                "基金代码": str(first["fund_code"]),
                "基金简称": str(first["fund_name"]),
                "前十大报告期": pd.to_datetime(first["report_date"]).strftime("%Y-%m-%d"),
                "前十大合计持仓权重": round(float(top10_weight * 100), 2),
                "组合加权过去一年股息率": round(float(weighted_dividend_yield * 100), 2),
                "连续正分红股票权重占比": round(float(continuous_weight_ratio * 100), 2),
            }
        ]
    )

    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    output = main()
    output.to_csv("result.csv", index=False, encoding="utf-8-sig", float_format="%.2f")
