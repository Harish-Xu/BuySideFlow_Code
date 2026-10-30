#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe

AS_OF_TS = "2025-05-16 23:59:59"
OUTPUT_PATH = Path(__file__).with_name("result.csv")
MARK_PRIORITY = {1: 5, 8: 4, 7: 3, 6: 2, 2: 1}


def fetch(sql: str, timeout: int = 600, retries: int = 0) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            df = query_to_dataframe(sql, max_rows=0, timeout=timeout)
            if df is None:
                raise ValueError("SQL execution returned None")
            return df
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            print(f"query_retry={attempt + 1}/{retries}: {type(exc).__name__}: {exc}", flush=True)
    raise last_error if last_error is not None else RuntimeError("SQL query failed")


def chunks(values: list[int], size: int = 5000):
    for i in range(0, len(values), size):
        yield values[i : i + size]


def fetch_chunks(sql_template: str, values: list[int], placeholder: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for part in chunks(values):
        codes = ",".join(str(int(x)) for x in part)
        frames.append(fetch(sql_template.format(**{placeholder: codes})))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def latest_by_period(df: pd.DataFrame, value_cols: list[str]) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["end_date"] = pd.to_datetime(df["end_date"])
    df["info_pub_date"] = pd.to_datetime(df["info_pub_date"])
    sort_cols = ["company_code", "end_date"]
    if "mark" in df.columns:
        df["_mark_priority"] = df["mark"].astype("Int64").map(MARK_PRIORITY).fillna(0)
        sort_cols.append("_mark_priority")
    sort_cols.append("info_pub_date")
    if "insert_time" in df.columns:
        df["insert_time"] = pd.to_datetime(df["insert_time"])
        sort_cols.append("insert_time")
    sort_cols.append("jsid")
    df = df.sort_values(sort_cols, na_position="first")
    df = df.drop_duplicates(["company_code", "end_date"], keep="last")
    return df[["company_code", "end_date", *value_cols]]


def pct_rank(s: pd.Series) -> pd.Series:
    n = s.notna().sum()
    if n <= 1:
        return pd.Series(0.0, index=s.index)
    return (s.rank(method="min", ascending=True) - 1) / (n - 1)


def main() -> pd.DataFrame:
    trade_date = "2025-05-16"

    stocks = fetch(
        f"""
SELECT
    s.INNERCODE AS inner_code,
    s.COMPANYCODE AS company_code,
    s.SECUCODE AS stock_code,
    COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name
FROM secumain s
LEFT JOIN (
    SELECT
        INNERCODE,
        change_type
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE AS change_type,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY ls.CHANGEDATE DESC,
                         COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
                         ls.JSID DESC
            ) AS rn
        FROM lc_liststatus ls
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
          AND ls.CHANGEDATE <= TIMESTAMP('{AS_OF_TS}')
          AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) <= TIMESTAMP('{AS_OF_TS}')
    ) t
    WHERE rn = 1
) lsa
  ON lsa.INNERCODE = s.INNERCODE
WHERE s.SECUCATEGORY = 1
  AND s.SECUMARKET IN (83, 90)
  AND s.LISTEDSECTOR IN (1, 2, 6)
  AND s.LISTEDDATE <= DATE_SUB(DATE('{trade_date}'), INTERVAL 3 YEAR)
  AND lsa.change_type IN (1, 3)
  AND NOT EXISTS (
      SELECT 1
      FROM cs_riskalert ra
      WHERE ra.INNERCODE = s.INNERCODE
        AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
        AND DATE(ra.IMPLEMENTDATE) <= DATE('{trade_date}')
        AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= TIMESTAMP('{AS_OF_TS}')
        AND (
            ra.REMOVEDATE IS NULL
            OR DATE(ra.REMOVEDATE) > DATE('{trade_date}')
            OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) > TIMESTAMP('{AS_OF_TS}')
        )
  );
"""
    )
    stocks = stocks.sort_values("inner_code").drop_duplicates("company_code", keep="first")
    if stocks.empty:
        result = pd.DataFrame()
        result.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
        print("<empty result>")
        return result

    company_codes = sorted(stocks["company_code"].dropna().astype(int).unique().tolist())
    inner_codes = sorted(stocks["inner_code"].dropna().astype(int).unique().tolist())

    industry = fetch_chunks(
        f"""
SELECT
    COMPANYCODE AS company_code,
    FIRSTINDUSTRYNAME AS sw_industry,
    INFOPUBLDATE AS info_pub_date,
    JSID AS jsid
FROM lc_exgindustry
WHERE COMPANYCODE IN ({{company_codes}})
	  AND STANDARD = 38
	  AND INFOPUBLDATE <= TIMESTAMP('{AS_OF_TS}')
	  AND COALESCE(INSERTTIME, INFOPUBLDATE) <= TIMESTAMP('{AS_OF_TS}')
	  AND (CANCELDATE IS NULL OR CANCELDATE > TIMESTAMP('{AS_OF_TS}'));
""",
        company_codes,
        "company_codes",
    )
    if not industry.empty:
        industry = industry.sort_values(
            ["company_code", "info_pub_date", "jsid"],
            ascending=[True, True, True],
        ).drop_duplicates("company_code", keep="last")[["company_code", "sw_industry"]]

    valuation = fetch_chunks(
        f"""
SELECT
    inner_code,
    dividend_yield
FROM (
    SELECT
        INNERCODE AS inner_code,
	        DIVIDENDRATIO AS dividend_yield,
        ROW_NUMBER() OVER (
            PARTITION BY INNERCODE
            ORDER BY TRADINGDAY DESC, JSID DESC
        ) AS rn
    FROM lc_dindicesforvaluation
    WHERE INNERCODE IN ({{inner_codes}})
      AND TRADINGDAY <= DATE('{trade_date}')
      AND TRADINGDAY >= DATE_SUB(DATE('{trade_date}'), INTERVAL 10 DAY)
) t
WHERE rn = 1;
""",
        inner_codes,
        "inner_codes",
    )

    liquidity = fetch_chunks(
        f"""
WITH trade_dates AS (
    SELECT trade_date
    FROM (
        SELECT DATE(t.TRADINGDATE) AS trade_date
        FROM qt_tradingdaynew t
        WHERE t.SECUMARKET = 83
          AND t.IFTRADINGDAY = 1
          AND t.TRADINGDATE <= DATE('{trade_date}')
        ORDER BY t.TRADINGDATE DESC
        LIMIT 60
    ) d
),
quotes AS (
    SELECT
        q.INNERCODE AS inner_code,
        DATE(q.TRADINGDAY) AS trade_date,
        q.TURNOVERVALUE AS turnover_value
    FROM qt_dailyquote q
    JOIN trade_dates d
      ON d.trade_date = q.TRADINGDAY
    WHERE q.INNERCODE IN ({{inner_codes}})
)
SELECT
    inner_code,
    SUM(COALESCE(turnover_value, 0)) / 60.0 AS avg_turnover_60d,
    60 AS turnover_days
FROM quotes
GROUP BY inner_code
HAVING SUM(COALESCE(turnover_value, 0)) / 60.0 >= 100000000;
""",
        inner_codes,
        "inner_codes",
    )

    base = (
        stocks.merge(liquidity[["inner_code"]], on="inner_code", how="inner")
        .merge(valuation, on="inner_code", how="inner")
        .merge(industry, on="company_code", how="left")
    )
    if base.empty:
        result = pd.DataFrame()
        result.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
        print("<empty result>")
        return result

    company_codes = sorted(base["company_code"].dropna().astype(int).unique().tolist())

    latest_fin = fetch_chunks(
        f"""
WITH ranked AS (
SELECT
	    COMPANYCODE AS company_code,
	    DATE(ENDDATE) AS end_date,
	    INFOPUBLDATE AS info_pub_date,
	    INSERTTIME AS insert_time,
	    JSID AS jsid,
    ROETTM AS roe_ttm,
    ROW_NUMBER() OVER (
        PARTITION BY COMPANYCODE
	        ORDER BY ENDDATE DESC, INFOPUBLDATE DESC, INSERTTIME DESC, JSID DESC
    ) AS rn
FROM lc_mainindexnew
WHERE COMPANYCODE IN ({{company_codes}})
	  AND INFOPUBLDATE <= TIMESTAMP('{AS_OF_TS}')
	  AND COALESCE(INSERTTIME, INFOPUBLDATE) <= TIMESTAMP('{AS_OF_TS}')
  AND ENDDATE >= DATE('2024-01-01')
  AND ENDDATE <= TIMESTAMP('{AS_OF_TS}')
)
	SELECT company_code, end_date, info_pub_date, insert_time, jsid, roe_ttm
FROM ranked
WHERE rn = 1;
""",
        company_codes,
        "company_codes",
    )

    annual_fin_raw = fetch_chunks(
        f"""
SELECT
    COMPANYCODE AS company_code,
    DATE(ENDDATE) AS end_date,
	    INFOPUBLDATE AS info_pub_date,
	    INSERTTIME AS insert_time,
	    JSID AS jsid,
    DIVIDENDPAIDRATIO AS dividend_paid_ratio
FROM lc_mainindexnew
WHERE COMPANYCODE IN ({{company_codes}})
	  AND INFOPUBLDATE <= TIMESTAMP('{AS_OF_TS}')
	  AND COALESCE(INSERTTIME, INFOPUBLDATE) <= TIMESTAMP('{AS_OF_TS}')
  AND ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'));
""",
        company_codes,
        "company_codes",
    )
    annual_fin = latest_by_period(annual_fin_raw, ["dividend_paid_ratio"])

    annual_main_raw = fetch_chunks(
        f"""
SELECT
    COMPANYCODE AS company_code,
    DATE(ENDDATE) AS end_date,
    INFOPUBLDATE AS info_pub_date,
    INSERTTIME AS insert_time,
    MARK AS mark,
    JSID AS jsid,
    NETOPERATECASHFLOW AS cfo,
    NPFROMPARENTCOMPANYOWNERS AS parent_np
FROM lc_maindatanew
WHERE COMPANYCODE IN ({{company_codes}})
	  AND INFOPUBLDATE <= TIMESTAMP('{AS_OF_TS}')
	  AND COALESCE(INSERTTIME, INFOPUBLDATE) <= TIMESTAMP('{AS_OF_TS}')
  AND ENDDATE <= TIMESTAMP('{AS_OF_TS}')
  AND COALESCE(ACCOUNTINGSTANDARDS, 1) = 1
  AND MARK IN (1, 8, 7, 6, 2)
  AND ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'));
""",
        company_codes,
        "company_codes",
    )
    annual_main = latest_by_period(annual_main_raw, ["cfo", "parent_np"])

    annual = annual_fin.merge(annual_main, on=["company_code", "end_date"], how="inner")
    annual = annual.sort_values(["company_code", "end_date"], ascending=[True, False])
    annual["annual_rank"] = annual.groupby("company_code").cumcount() + 1
    annual3 = annual[annual["annual_rank"] <= 3].copy()
    annual_stats = annual3.groupby("company_code").agg(
        avg_dividend_paid_ratio=("dividend_paid_ratio", "mean"),
        dividend_ok_years=("dividend_paid_ratio", lambda x: ((x >= 30) & (x <= 60)).sum()),
        annual_count=("end_date", "count"),
        cfo_count=("cfo", "count"),
        parent_np_count=("parent_np", "count"),
        cfo_sum_3y=("cfo", "sum"),
        parent_np_sum_3y=("parent_np", "sum"),
    )
    annual_stats["cashflow_coverage"] = (
        annual_stats["cfo_sum_3y"] / annual_stats["parent_np_sum_3y"]
    )
    annual_stats.loc[annual_stats["parent_np_sum_3y"] == 0, "cashflow_coverage"] = pd.NA
    annual_stats = annual_stats.reset_index()

    data = (
        base.merge(latest_fin[["company_code", "roe_ttm"]], on="company_code", how="left")
        .merge(annual_stats, on="company_code", how="left")
    )

    core = data[
        (data["dividend_yield"] > 3)
        & (data["roe_ttm"] > 10)
        & (data["annual_count"] == 3)
        & (data["cfo_count"] == 3)
        & (data["parent_np_count"] == 3)
        & (data["dividend_ok_years"] == 3)
        & (data["parent_np_sum_3y"] != 0)
        & (data["cfo_sum_3y"] > data["parent_np_sum_3y"])
    ].copy()
    if not core.empty:
        core["score"] = (
            pct_rank(core["dividend_yield"])
            + pct_rank(core["roe_ttm"])
            + pct_rank(core["cashflow_coverage"])
        )

    result = (
        core.sort_values(["score", "stock_code"], ascending=[False, True]).head(20)
        if not core.empty
        else pd.DataFrame()
    )
    if not result.empty:
        result = result[
            [
                "stock_code",
                "stock_name",
                "sw_industry",
                "score",
                "dividend_yield",
                "roe_ttm",
                "cashflow_coverage",
                "avg_dividend_paid_ratio",
            ]
        ].rename(
            columns={
                "stock_code": "股票代码",
                "stock_name": "股票简称",
                "sw_industry": "申万一级行业",
                "score": "评分",
                "dividend_yield": "股息率(%)",
                "roe_ttm": "TTM ROE(%)",
                "cashflow_coverage": "现金流覆盖率",
                "avg_dividend_paid_ratio": "近3年平均分红率(%)",
            }
        )
        result["评分"] = result["评分"].round(4)
        result["现金流覆盖率"] = result["现金流覆盖率"].round(4)
        for col in ["股息率(%)", "TTM ROE(%)", "近3年平均分红率(%)"]:
            result[col] = result[col].round(2)
    result.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    main()
