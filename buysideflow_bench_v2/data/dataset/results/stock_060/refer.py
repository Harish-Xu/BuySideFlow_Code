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


AS_OF_DATE = "2025-12-31"
AS_OF_TS = "2025-12-31 23:59:59"
ANNUAL_DATES = ["2023-12-31", "2024-12-31"]
RECENT_DATES = ["2025-03-31", "2025-06-30", "2025-09-30"]
TTM_SUPPORT_DATES = ["2024-03-31", "2024-06-30", "2024-09-30"]


def _sql_list(values: list[int | str]) -> str:
    return ",".join(f"'{v}'" if isinstance(v, str) else str(int(v)) for v in values)


def _mark_priority_sql(alias: str = "m") -> str:
    return f"""
                CASE
                    WHEN {alias}.MARK = 1 THEN 1
                    WHEN {alias}.MARK = 8 THEN 2
                    WHEN {alias}.MARK = 7 THEN 3
                    WHEN {alias}.MARK = 6 THEN 4
                    WHEN {alias}.MARK = 2 THEN 5
                    ELSE 9
                END
    """


def _fetch_observation_pool() -> pd.DataFrame:
    sql = f"""
WITH params AS (
    SELECT DATE('{AS_OF_DATE}') AS as_of_date,
           TIMESTAMP('{AS_OF_TS}') AS as_of_dt
),
list_status_asof AS (
    SELECT INNERCODE, change_type
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
        JOIN params p
          ON ls.CHANGEDATE <= p.as_of_dt
         AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) <= p.as_of_dt
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
stock_base AS (
    SELECT
        INNERCODE,
        COMPANYCODE,
        SECUCODE,
        company
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUCODE,
            COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS company,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.SECUCODE, s.INNERCODE
            ) AS rn
        FROM secumain s
        JOIN params p
          ON s.LISTEDDATE <= p.as_of_date
        LEFT JOIN list_status_asof lsa
          ON lsa.INNERCODE = s.INNERCODE
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2, 6)
  AND lsa.change_type IN (1, 3)
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              WHERE ra.INNERCODE = s.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND DATE(ra.IMPLEMENTDATE) <= p.as_of_date
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= p.as_of_dt
                AND (
                    ra.REMOVEDATE IS NULL
                    OR DATE(ra.REMOVEDATE) > p.as_of_date
                    OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) > p.as_of_dt
                )
          )
    ) t
    WHERE rn = 1
),
industry_asof AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC,
                         e.CANCELDATE DESC,
                         e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_dt
         AND COALESCE(e.INSERTTIME, e.INFOPUBLDATE) <= p.as_of_dt
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_dt)
    ) t
    WHERE rn = 1
),
latest_interest_cover AS (
    SELECT COMPANYCODE, INTERESTCOVER
    FROM (
        SELECT
            m.COMPANYCODE,
            m.INTERESTCOVER,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE
                ORDER BY m.ENDDATE DESC,
                         m.INFOPUBLDATE DESC,
                         COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                         m.JSID DESC
            ) AS rn
        FROM lc_mainindexnew m
        JOIN stock_base sb
          ON sb.COMPANYCODE = m.COMPANYCODE
        JOIN params p
          ON m.INFOPUBLDATE <= p.as_of_dt
         AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) <= p.as_of_dt
        WHERE m.ENDDATE <= DATE('2025-09-30')
          AND m.INTERESTCOVER IS NOT NULL
    ) t
    WHERE rn = 1
)
SELECT
    sb.INNERCODE,
    sb.COMPANYCODE,
    sb.SECUCODE,
    sb.company,
    li.INTERESTCOVER
FROM stock_base sb
JOIN industry_asof ia
  ON ia.COMPANYCODE = sb.COMPANYCODE
JOIN latest_interest_cover li
  ON li.COMPANYCODE = sb.COMPANYCODE
WHERE ia.FIRSTINDUSTRYNAME NOT IN ('银行', '非银金融')
  AND li.INTERESTCOVER < 2
"""
    return query_to_dataframe(sql, max_rows=0, timeout=300)


def _fetch_trade_days(dates: list[str]) -> pd.DataFrame:
    sql = f"""
SELECT ref_date, MAX(TRADINGDATE) AS trade_day
FROM (
    SELECT DATE('{dates[0]}') AS ref_date
    {" ".join(f"UNION ALL SELECT DATE('{d}')" for d in dates[1:])}
) d
JOIN qt_tradingdaynew t
  ON t.TRADINGDATE <= d.ref_date
WHERE t.IFTRADINGDAY = 1
  AND t.SECUMARKET IN (83, 90)
GROUP BY ref_date
"""
    return query_to_dataframe(sql, max_rows=0, timeout=120)


def _fetch_main(company_codes: list[int], dates: list[str]) -> pd.DataFrame:
    sql = f"""
SELECT COMPANYCODE, ENDDATE, TOTALASSETS, TOTALLIABILITY, OPERATINGREENUE
FROM (
    SELECT
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS ENDDATE,
        m.TOTALASSETS,
        m.TOTALLIABILITY,
        m.OPERATINGREENUE,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
            ORDER BY {_mark_priority_sql("m")},
                     m.INFOPUBLDATE DESC,
                     COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                     m.JSID DESC
        ) AS rn
    FROM lc_maindatanew m
    WHERE m.COMPANYCODE IN ({_sql_list(company_codes)})
      AND m.INFOPUBLDATE <= TIMESTAMP('{AS_OF_TS}')
      AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) <= TIMESTAMP('{AS_OF_TS}')
      AND m.MARK IN (1, 8, 7, 6, 2)
      AND COALESCE(m.ACCOUNTINGSTANDARDS, 1) = 1
      AND DATE(m.ENDDATE) IN ({_sql_list(dates)})
      AND m.TOTALASSETS IS NOT NULL
      AND m.TOTALLIABILITY IS NOT NULL
      AND m.OPERATINGREENUE IS NOT NULL
) t
WHERE rn = 1
"""
    return query_to_dataframe(sql, max_rows=0, timeout=300)


def _fetch_derivative(company_codes: list[int], dates: list[str]) -> pd.DataFrame:
    sql = f"""
SELECT
    COMPANYCODE,
    ENDDATE,
    WORKINGCAPITAL,
    EBIT,
    RETAINEDEARNINGASSET
FROM (
    SELECT
        i.COMPANYCODE,
        DATE(i.ENDDATE) AS ENDDATE,
        i.WORKINGCAPITAL,
        i.EBIT,
        i.RETAINEDEARNINGASSET,
        ROW_NUMBER() OVER (
            PARTITION BY i.COMPANYCODE, DATE(i.ENDDATE)
            ORDER BY i.INFOPUBLDATE DESC,
                     COALESCE(i.INSERTTIME, i.INFOPUBLDATE) DESC,
                     i.JSID DESC
        ) AS rn
    FROM lc_mainindexnew i
    WHERE i.COMPANYCODE IN ({_sql_list(company_codes)})
      AND i.INFOPUBLDATE <= TIMESTAMP('{AS_OF_TS}')
      AND COALESCE(i.INSERTTIME, i.INFOPUBLDATE) <= TIMESTAMP('{AS_OF_TS}')
      AND DATE(i.ENDDATE) IN ({_sql_list(dates)})
      AND i.WORKINGCAPITAL IS NOT NULL
      AND i.EBIT IS NOT NULL
      AND i.RETAINEDEARNINGASSET IS NOT NULL
) t
WHERE rn = 1
"""
    return query_to_dataframe(sql, max_rows=0, timeout=300)


def _fetch_market_value(innercodes: list[int], trade_days: list[str]) -> pd.DataFrame:
    sql = f"""
SELECT INNERCODE, TRADINGDAY, TOTALMV
FROM (
    SELECT
        v.INNERCODE,
        DATE(v.TRADINGDAY) AS TRADINGDAY,
        v.TOTALMV,
        ROW_NUMBER() OVER (
            PARTITION BY v.INNERCODE, DATE(v.TRADINGDAY)
            ORDER BY v.JSID DESC
        ) AS rn
    FROM lc_dindicesforvaluation v
    WHERE v.INNERCODE IN ({_sql_list(innercodes)})
      AND DATE(v.TRADINGDAY) IN ({_sql_list(trade_days)})
      AND COALESCE(v.INSERTTIME, v.TRADINGDAY) <= TIMESTAMP('{AS_OF_TS}')
      AND v.TOTALMV IS NOT NULL
) t
WHERE rn = 1
"""
    return query_to_dataframe(sql, max_rows=0, timeout=300)


def _add_ttm_flow(df: pd.DataFrame, value_col: str, output_col: str) -> pd.DataFrame:
    target_dates = ANNUAL_DATES + RECENT_DATES
    wide = df.pivot_table(index="COMPANYCODE", columns="ENDDATE", values=value_col, aggfunc="last")
    ttm = pd.DataFrame(index=wide.index)

    for end_date in ANNUAL_DATES:
        ttm[end_date] = wide[end_date] if end_date in wide else np.nan

    for end_date in RECENT_DATES:
        prior_same_period = f"{int(end_date[:4]) - 1}{end_date[4:]}"
        prior_annual = f"{int(end_date[:4]) - 1}-12-31"
        if end_date in wide and prior_annual in wide and prior_same_period in wide:
            ttm[end_date] = wide[end_date] + wide[prior_annual] - wide[prior_same_period]
        else:
            ttm[end_date] = np.nan

    long = ttm[target_dates].reset_index().melt(
        id_vars="COMPANYCODE",
        var_name="ENDDATE",
        value_name=output_col,
    )
    return df.merge(long, on=["COMPANYCODE", "ENDDATE"], how="left")


def _calc_z(main_df: pd.DataFrame, deriv_df: pd.DataFrame, mv_df: pd.DataFrame, map_df: pd.DataFrame) -> pd.DataFrame:
    df = main_df.merge(deriv_df, on=["COMPANYCODE", "ENDDATE"], how="inner")
    for col in [
        "TOTALASSETS",
        "TOTALLIABILITY",
        "OPERATINGREENUE",
        "WORKINGCAPITAL",
        "EBIT",
        "RETAINEDEARNINGASSET",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = _add_ttm_flow(df, "OPERATINGREENUE", "OPERATINGREENUE_TTM")
    df = _add_ttm_flow(df, "EBIT", "EBIT_TTM")
    df = df[df["ENDDATE"].isin(ANNUAL_DATES + RECENT_DATES)].copy()
    df = df.merge(map_df[["COMPANYCODE", "INNERCODE"]], on="COMPANYCODE", how="inner")
    df = df.merge(mv_df, left_on=["INNERCODE", "trade_day"], right_on=["INNERCODE", "TRADINGDAY"], how="inner")
    for col in ["TOTALMV", "OPERATINGREENUE_TTM", "EBIT_TTM"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["altman_z"] = (
        1.2 * df["WORKINGCAPITAL"] / df["TOTALASSETS"].replace(0, np.nan)
        + 1.4 * df["RETAINEDEARNINGASSET"] / 100.0
        + 3.3 * df["EBIT_TTM"] / df["TOTALASSETS"].replace(0, np.nan)
        + 0.6 * df["TOTALMV"] / df["TOTALLIABILITY"].replace(0, np.nan)
        + df["OPERATINGREENUE_TTM"] / df["TOTALASSETS"].replace(0, np.nan)
    )
    return df[["COMPANYCODE", "ENDDATE", "altman_z"]]


def _bucket(value: float) -> str:
    if value < 1.8:
        return "高风险区"
    if value <= 3:
        return "灰区"
    return "安全区"


def main() -> pd.DataFrame:
    out_path = Path(__file__).with_name("result.csv")
    pool = _fetch_observation_pool()
    pool["COMPANYCODE"] = pool["COMPANYCODE"].astype(int)
    pool["INNERCODE"] = pool["INNERCODE"].astype(int)
    company_codes = pool["COMPANYCODE"].tolist()
    innercodes = pool["INNERCODE"].tolist()

    target_dates = ANNUAL_DATES + RECENT_DATES
    fetch_dates = sorted(set(target_dates + TTM_SUPPORT_DATES))
    trade_days = _fetch_trade_days(target_dates)
    trade_days["ref_date"] = pd.to_datetime(trade_days["ref_date"]).dt.date.astype(str)
    trade_days["trade_day"] = pd.to_datetime(trade_days["trade_day"]).dt.date.astype(str)
    date_to_trade = dict(zip(trade_days["ref_date"], trade_days["trade_day"]))

    main_df = _fetch_main(company_codes, fetch_dates)
    deriv_df = _fetch_derivative(company_codes, fetch_dates)
    mv_df = _fetch_market_value(innercodes, sorted(set(date_to_trade.values())))
    main_df["ENDDATE"] = pd.to_datetime(main_df["ENDDATE"]).dt.date.astype(str)
    deriv_df["ENDDATE"] = pd.to_datetime(deriv_df["ENDDATE"]).dt.date.astype(str)
    mv_df["TRADINGDAY"] = pd.to_datetime(mv_df["TRADINGDAY"]).dt.date.astype(str)
    main_df["trade_day"] = main_df["ENDDATE"].map(date_to_trade)

    z = _calc_z(main_df, deriv_df, mv_df, pool)
    pivot = z.pivot_table(index="COMPANYCODE", columns="ENDDATE", values="altman_z", aggfunc="last")
    pivot = pivot.dropna(subset=ANNUAL_DATES)
    pivot["2023风险分层"] = pivot[ANNUAL_DATES[0]].map(_bucket)
    pivot["2024风险分层"] = pivot[ANNUAL_DATES[1]].map(_bucket)
    pivot["continued_deterioration"] = (
        pivot[RECENT_DATES[0]].notna()
        & pivot[RECENT_DATES[1]].notna()
        & pivot[RECENT_DATES[2]].notna()
        & (pivot[RECENT_DATES[0]] < pivot[ANNUAL_DATES[1]])
        & (pivot[RECENT_DATES[1]] < pivot[RECENT_DATES[0]])
        & (pivot[RECENT_DATES[2]] < pivot[RECENT_DATES[1]])
    ).astype(int)

    observation_pool_size = len(pivot)
    result = (
        pivot.groupby(["2023风险分层", "2024风险分层"], dropna=False)
        .agg(公司数=("continued_deterioration", "size"), **{"2025延续恶化公司数": ("continued_deterioration", "sum")})
        .reset_index()
    )
    result["占观察池比例(%)"] = (
        (result["公司数"] / observation_pool_size * 100).round(2) if observation_pool_size else np.nan
    )
    result["路径内延续恶化占比(%)"] = (result["2025延续恶化公司数"] / result["公司数"] * 100).round(2)
    order = {"安全区": 0, "灰区": 1, "高风险区": 2}
    result = result.sort_values(
        by=["2023风险分层", "2024风险分层"],
        key=lambda s: s.map(order),
    )
    result = result[
        ["2023风险分层", "2024风险分层", "公司数", "占观察池比例(%)", "2025延续恶化公司数", "路径内延续恶化占比(%)"]
    ]
    result.to_csv(out_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    main()
