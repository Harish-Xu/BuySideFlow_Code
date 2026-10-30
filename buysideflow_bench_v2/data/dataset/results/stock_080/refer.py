#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe

OUTPUT_PATH = Path(__file__).with_name("result.csv")


SQL = r"""
WITH observations AS (
    SELECT DATE('2025-04-30') AS observation_date,
           DATE('2025-03-31') AS current_period,
           DATE('2024-03-31') AS prior_period
    UNION ALL
    SELECT DATE('2025-08-31'), DATE('2025-06-30'), DATE('2024-06-30')
    UNION ALL
    SELECT DATE('2025-10-31'), DATE('2025-09-30'), DATE('2024-09-30')
),
target_index AS (
    SELECT MIN(INNERCODE) AS index_inner_code
    FROM secumain
    WHERE SECUCODE = '399296'
      AND SECUCATEGORY = 4
      AND SECUMARKET = 90
),
components AS (
    SELECT DISTINCT
        o.observation_date,
        o.current_period,
        o.prior_period,
        sm.COMPANYCODE AS company_code
    FROM observations o
    JOIN target_index ti
    JOIN lc_indexcomponent ic
      ON ic.INDEXINNERCODE = ti.index_inner_code
     AND ic.INDATE <= o.observation_date
     AND (ic.OUTDATE IS NULL OR ic.OUTDATE > o.observation_date)
    JOIN secumain sm
      ON sm.INNERCODE = ic.SECUINNERCODE
     AND sm.SECUCATEGORY = 1
),
industry_ranked AS (
    SELECT
        c.observation_date,
        c.company_code,
        i.FIRSTINDUSTRYNAME AS first_industry_name,
        ROW_NUMBER() OVER (
            PARTITION BY c.observation_date, c.company_code
            ORDER BY i.INFOPUBLDATE DESC,
                     i.INSERTTIME DESC,
                     i.JSID DESC
        ) AS rn
    FROM components c
    JOIN lc_exgindustry i
      ON i.COMPANYCODE = c.company_code
     AND i.STANDARD = 38
     AND i.INFOPUBLDATE < DATE_ADD(c.observation_date, INTERVAL 1 DAY)
     AND (i.CANCELDATE IS NULL OR i.CANCELDATE > c.observation_date)
),
eligible_components AS (
    SELECT c.*
    FROM components c
    JOIN industry_ranked i
      ON i.observation_date = c.observation_date
     AND i.company_code = c.company_code
     AND i.rn = 1
    WHERE i.first_industry_name <> '非银金融'
),
financial_ranked AS (
    SELECT
        c.observation_date,
        c.current_period,
        c.prior_period,
        c.company_code,
        DATE(f.ENDDATE) AS end_date,
        f.NPPARENTCOMPANYOWNERSTTM AS net_profit_ttm,
        f.NETOPERATECASHFLOWTTM AS operating_cash_flow_ttm,
        ROW_NUMBER() OVER (
            PARTITION BY c.observation_date, c.company_code, f.ENDDATE
            ORDER BY f.INFOPUBLDATE DESC,
                     f.UPDATETIME DESC,
                     f.JSID DESC
        ) AS rn
    FROM eligible_components c
    JOIN lc_derivativedata f
      ON f.COMPANYCODE = c.company_code
     AND f.ENDDATE IN (c.current_period, c.prior_period)
     AND f.INFOPUBLDATE < DATE_ADD(c.observation_date, INTERVAL 1 DAY)
),
financial_pairs AS (
    SELECT
        observation_date,
        company_code,
        MAX(CASE WHEN end_date = current_period THEN net_profit_ttm END) AS current_net_profit,
        MAX(CASE WHEN end_date = current_period THEN operating_cash_flow_ttm END) AS current_ocf,
        MAX(CASE WHEN end_date = prior_period THEN net_profit_ttm END) AS prior_net_profit,
        MAX(CASE WHEN end_date = prior_period THEN operating_cash_flow_ttm END) AS prior_ocf
    FROM financial_ranked
    WHERE rn = 1
    GROUP BY observation_date, company_code
),
comparable AS (
    SELECT *
    FROM financial_pairs
    WHERE current_net_profit > 0
      AND prior_net_profit > 0
      AND current_ocf IS NOT NULL
      AND prior_ocf IS NOT NULL
)
SELECT
    DATE_FORMAT(observation_date, '%Y-%m-%d') AS `观察日`,
    COUNT(*) AS `可比公司数`,
    ROUND(
        100.0 * SUM(current_ocf > current_net_profit) / COUNT(*),
        2
    ) AS `现金覆盖比例`,
    ROUND(
        100.0 * SUM(
            prior_ocf <= prior_net_profit
            AND current_ocf > current_net_profit
        ) / COUNT(*),
        2
    ) AS `现金覆盖改善比例`
FROM comparable
GROUP BY observation_date
ORDER BY observation_date;
"""


def main() -> pd.DataFrame:
    result = query_to_dataframe(SQL, max_rows=0, timeout=600)
    expected_columns = ["观察日", "可比公司数", "现金覆盖比例", "现金覆盖改善比例"]
    expected_dates = ["2025-04-30", "2025-08-31", "2025-10-31"]

    if list(result.columns) != expected_columns:
        raise ValueError(f"unexpected output columns: {list(result.columns)}")
    if result["观察日"].astype(str).tolist() != expected_dates:
        raise ValueError("one or more observation dates have no comparable companies")

    result["可比公司数"] = pd.to_numeric(result["可比公司数"], errors="raise").astype(int)
    for column in ["现金覆盖比例", "现金覆盖改善比例"]:
        result[column] = pd.to_numeric(result[column], errors="raise")

    result.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_string(index=False, float_format=lambda value: f"{value:.2f}"))
    return result


if __name__ == "__main__":
    main()
