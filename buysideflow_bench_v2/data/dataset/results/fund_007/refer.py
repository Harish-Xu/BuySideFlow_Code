#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from sweagent.text2sql.db_connector import get_db_connector
except ImportError:
    from sweagent.text2sql.db_connector import get_db_connector


DB = get_db_connector()

ANCHOR_DAY = "2025-12-31"
NAV_START_DAY = "2023-01-01"
THIRD_ASSET_CAT_CODE = 130202
TMP_FUND_POOL = "tmp_q7_fund_pool"
OUTPUT_PATH = Path(__file__).with_name("result.csv")


FUND_POOL_SELECT_SQL = f"""
WITH params AS (
    SELECT
        DATE('{ANCHOR_DAY}') AS anchor_day,
        DATE('{NAV_START_DAY}') AS nav_start_day,
        DATE_SUB(DATE('{ANCHOR_DAY}'), INTERVAL 3 YEAR) AS manager_cutoff_day
),
type_asof AS (
    SELECT
        t.INNERCODE,
        t.THIRDASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS rn
    FROM mf_jyfundtype t
    JOIN params p
      ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= p.anchor_day
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.anchor_day)
      AND t.INSERTTIME < DATE_ADD(p.anchor_day, INTERVAL 1 DAY)
),
current_managers AS (
    SELECT
        m.INNERCODE,
        MAX(m.ACCESSIONDATE) AS latest_current_accession_date
    FROM mf_fundmanagernew m
    JOIN params p
      ON 1 = 1
    WHERE m.POSTNAME = 1
      AND m.ACCESSIONDATE IS NOT NULL
      AND m.ACCESSIONDATE <= p.anchor_day
      AND (m.DIMISSIONDATE IS NULL OR m.DIMISSIONDATE > p.anchor_day)
      AND m.INFOPUBLDATE < DATE_ADD(p.anchor_day, INTERVAL 1 DAY)
    GROUP BY m.INNERCODE
)
SELECT
    fa.INNERCODE,
    fa.SECURITYCODE AS fund_code,
    COALESCE(sm.CHINAMEABBR, sm.SECUABBR, sm.CHINAME, fa.SECURITYCODE) AS fund_name
FROM mf_fundarchives fa
JOIN secumain sm
  ON sm.INNERCODE = fa.INNERCODE
 AND sm.SECUCATEGORY = 8
JOIN type_asof ta
  ON ta.INNERCODE = fa.INNERCODE
 AND ta.rn = 1
JOIN current_managers cm
  ON cm.INNERCODE = fa.INNERCODE
JOIN params p
  ON 1 = 1
WHERE ta.THIRDASSETCATCODE = {THIRD_ASSET_CAT_CODE}
  AND fa.TYPE = 2
  AND COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
  AND fa.ESTABLISHMENTDATE <= p.manager_cutoff_day
  AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.anchor_day)
  AND cm.latest_current_accession_date <= p.manager_cutoff_day
"""

SQL_CREATE_FUND_POOL = f"""
CREATE TEMPORARY TABLE {TMP_FUND_POOL} AS
{FUND_POOL_SELECT_SQL}
"""

SQL_INDEX_FUND_POOL = f"""
ALTER TABLE {TMP_FUND_POOL}
ADD INDEX idx_q7_fund_pool_innercode (INNERCODE)
"""

NAV_SQL = f"""
SELECT
    n.INNERCODE,
    fp.fund_code,
    fp.fund_name,
    DATE(n.TRADINGDAY) AS trading_day,
    n.UNITNVRESTORED,
    n.INSERTTIME,
    n.ID
FROM mf_fundnetvalueretrans n
JOIN {TMP_FUND_POOL} fp
  ON fp.INNERCODE = n.INNERCODE
WHERE n.UNITNVRESTORED IS NOT NULL
  AND DATE(n.TRADINGDAY) BETWEEN DATE('{NAV_START_DAY}') AND DATE('{ANCHOR_DAY}')
  AND n.INSERTTIME < DATE_ADD(DATE('{ANCHOR_DAY}'), INTERVAL 1 DAY)
ORDER BY n.INNERCODE, DATE(n.TRADINGDAY), n.TRADINGDAY, n.INSERTTIME, n.ID
"""


def run_statement(conn, sql: str, *, timeout: int) -> None:
    result = DB.execute_query_on_connection(
        conn,
        sql,
        max_rows=0,
        timeout=timeout,
        read_only=False,
    )
    if not result["success"]:
        raise RuntimeError(result["error"])


def fetch_nav_data() -> pd.DataFrame:
    with DB.connection() as conn:
        for sql, timeout in [
            (f"DROP TEMPORARY TABLE IF EXISTS {TMP_FUND_POOL}", 30),
            (SQL_CREATE_FUND_POOL, 180),
            (SQL_INDEX_FUND_POOL, 60),
        ]:
            run_statement(conn, sql, timeout=timeout)

        nav_df = DB.execute_sql_to_dataframe_on_connection(
            conn,
            NAV_SQL,
            max_rows=0,
            timeout=600,
            read_only=True,
        )

    if nav_df.empty:
        raise ValueError("未取到 2023 年以来复权单位净值数据。")

    nav_df["INNERCODE"] = pd.to_numeric(nav_df["INNERCODE"], errors="coerce")
    nav_df["trading_day"] = pd.to_datetime(nav_df["trading_day"], errors="coerce")
    nav_df["UNITNVRESTORED"] = pd.to_numeric(nav_df["UNITNVRESTORED"], errors="coerce")
    nav_df["INSERTTIME"] = pd.to_datetime(nav_df["INSERTTIME"], errors="coerce")
    nav_df["ID"] = pd.to_numeric(nav_df["ID"], errors="coerce")
    nav_df = nav_df.dropna(
        subset=["INNERCODE", "fund_code", "fund_name", "trading_day", "UNITNVRESTORED"]
    ).copy()
    nav_df = nav_df[nav_df["UNITNVRESTORED"] > 0].copy()
    nav_df["INNERCODE"] = nav_df["INNERCODE"].astype(int)

    nav_df = (
        nav_df.sort_values(
            ["INNERCODE", "trading_day", "INSERTTIME", "ID"],
            ascending=[True, True, True, True],
            kind="mergesort",
            na_position="first",
        )
        .drop_duplicates(subset=["INNERCODE", "trading_day"], keep="last")
        .reset_index(drop=True)
    )
    return nav_df[["INNERCODE", "fund_code", "fund_name", "trading_day", "UNITNVRESTORED"]].copy()


def calc_calmar_metrics(nav_df: pd.DataFrame) -> pd.DataFrame:
    nav_df = nav_df.sort_values(["INNERCODE", "trading_day"], kind="stable").copy()
    nav_df["running_peak"] = nav_df.groupby("INNERCODE", sort=False)["UNITNVRESTORED"].cummax()
    nav_df["drawdown_abs"] = np.where(
        nav_df["running_peak"] > 0,
        (nav_df["running_peak"] - nav_df["UNITNVRESTORED"]) / nav_df["running_peak"],
        np.nan,
    )

    metric_df = (
        nav_df.groupby("INNERCODE", sort=False, as_index=False)
        .agg(
            fund_code=("fund_code", "first"),
            fund_name=("fund_name", "first"),
            first_day=("trading_day", "first"),
            last_day=("trading_day", "last"),
            first_nav=("UNITNVRESTORED", "first"),
            last_nav=("UNITNVRESTORED", "last"),
            nav_observation_count=("UNITNVRESTORED", "size"),
            max_drawdown_abs=("drawdown_abs", "max"),
        )
        .copy()
    )

    metric_df["daily_return_count"] = metric_df["nav_observation_count"] - 1
    metric_df = metric_df[
        (metric_df["first_nav"] > 0)
        & (metric_df["last_nav"] > 0)
        & (metric_df["daily_return_count"] > 0)
        & (metric_df["max_drawdown_abs"] > 0)
    ].copy()

    metric_df["annualized_return"] = (
        (metric_df["last_nav"] / metric_df["first_nav"])
        ** (250.0 / metric_df["daily_return_count"])
        - 1.0
    )
    metric_df["calmar_ratio"] = metric_df["annualized_return"] / metric_df["max_drawdown_abs"]
    metric_df = metric_df[metric_df["calmar_ratio"] > 4].copy()

    result_df = metric_df.rename(
        columns={
            "fund_code": "基金代码",
            "fund_name": "基金简称",
            "annualized_return": "2023 年以来年化收益率",
            "max_drawdown_abs": "2023 年以来最大回撤",
            "calmar_ratio": "卡玛比率",
        }
    )[["基金代码", "基金简称", "2023 年以来年化收益率", "2023 年以来最大回撤", "卡玛比率"]]

    if result_df.empty:
        raise ValueError("未找到符合条件的二级债基重点观察池。")

    result_df = result_df.sort_values(["卡玛比率", "基金代码"], ascending=[False, True]).reset_index(drop=True)
    for col in ["2023 年以来年化收益率", "2023 年以来最大回撤", "卡玛比率"]:
        result_df[col] = result_df[col].round(4)
    return result_df


def main() -> pd.DataFrame:
    nav_df = fetch_nav_data()
    result_df = calc_calmar_metrics(nav_df)
    result_df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig", float_format="%.4f")
    print(result_df.to_string(index=False))
    return result_df


if __name__ == "__main__":
    main()
