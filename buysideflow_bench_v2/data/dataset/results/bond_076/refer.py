#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import get_db_connector


AS_OF_DATE = "2025-12-31"
AS_OF_NEXT_DATE = "2026-01-01"
MIN_REMAINING_YEARS = 1.0
MAX_REMAINING_YEARS = 3.0

RESULT_COLUMNS = [
    "债券代码",
    "债券简称",
    "发行人",
    "剩余期限",
    "估值收益率",
    "当前套息空间",
    "情景套息空间",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="1-3 年 AAA 企业类信用债 FDR007 套息筛选")
    parser.add_argument("--output-path", default=str(Path(__file__).with_name("result.csv")))
    return parser.parse_args()


def exec_df(connector, conn, sql: str, timeout: int = 600) -> pd.DataFrame:
    return connector.execute_sql_to_dataframe_on_connection(conn, sql, max_rows=0, timeout=timeout)


def create_temp_tables(connector, conn) -> None:
    for table in [
        "tmp_b076_fdr",
        "tmp_b076_credit_bonds",
        "tmp_b076_valuation",
        "tmp_b076_rating",
        "tmp_b076_display",
    ]:
        exec_df(connector, conn, f"DROP TEMPORARY TABLE IF EXISTS {table}")

    exec_df(
        connector,
        conn,
        f"""
        CREATE TEMPORARY TABLE tmp_b076_fdr AS
        SELECT
            DATE(ENDDATE) AS rate_date,
            FDR007 AS fdr007_pct
        FROM bond_baseratereference
        WHERE ENDDATE <= DATE('{AS_OF_DATE}')
          AND FDR007 IS NOT NULL
          AND (INSERTTIME IS NULL OR INSERTTIME < DATE('{AS_OF_NEXT_DATE}'))
        ORDER BY ENDDATE DESC, ID DESC
        LIMIT 1;
        """,
        timeout=120,
    )
    fdr = exec_df(connector, conn, "SELECT COUNT(*) AS n FROM tmp_b076_fdr", timeout=60)
    if int(fdr.loc[0, "n"]) != 1:
        raise ValueError(f"{AS_OF_DATE} 之前未找到可用 FDR007")

    exec_df(
        connector,
        conn,
        f"""
        CREATE TEMPORARY TABLE tmp_b076_credit_bonds AS
        SELECT DISTINCT
            CAST(MAINCODE AS UNSIGNED) AS main_code,
            CAST(INNERCODE AS UNSIGNED) AS inner_code,
            SECUCODE AS bond_code,
            SECUABBR AS bond_name,
            ISSUER AS issuer,
            SECUMARKET AS secu_market
        FROM bond_code FORCE INDEX (IX_BOND_CODE)
        WHERE MAINCODE IS NOT NULL
          AND INNERCODE IS NOT NULL
          AND (
              BONDTYPELEVEL1 IN (1600,1700,1800,2500,2600,2800,2900,3100,3200,3300,3700)
              OR BONDTYPELEVEL2 IN (1901,1903)
          )
          AND (LISTEDDATE IS NULL OR LISTEDDATE < DATE('{AS_OF_NEXT_DATE}'))
          AND (INTERESTENDDATE IS NULL OR INTERESTENDDATE >= DATE('{AS_OF_DATE}'))
          AND (DELISTDATE IS NULL OR DELISTDATE >= DATE('{AS_OF_DATE}'));
        """,
        timeout=300,
    )
    exec_df(
        connector,
        conn,
        """
        ALTER TABLE tmp_b076_credit_bonds
        ADD INDEX idx_inner (inner_code),
        ADD INDEX idx_main (main_code);
        """,
        timeout=120,
    )

    exec_df(
        connector,
        conn,
        f"""
        CREATE TEMPORARY TABLE tmp_b076_valuation AS
        SELECT
            main_code,
            inner_code,
            valuation_date,
            valuation_yield_pct,
            remaining_years
        FROM (
            SELECT
                cb.main_code,
                cb.inner_code,
                DATE(v.ENDDATE) AS valuation_date,
                v.VPYIELD AS valuation_yield_pct,
                v.TRUEREMAINMATURITY AS remaining_years,
                ROW_NUMBER() OVER (
                    PARTITION BY cb.main_code
                    ORDER BY
                        CASE WHEN cb.secu_market = 89 THEN 0 ELSE 1 END,
                        CASE WHEN cb.inner_code = cb.main_code THEN 0 ELSE 1 END,
                        COALESCE(cb.bond_code, '') ASC,
                        cb.inner_code ASC,
                        v.UPDATETIME DESC, v.ID DESC
                ) AS rn
            FROM tmp_b076_credit_bonds cb
            JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
              ON v.INNERCODE = cb.inner_code
            WHERE v.ENDDATE >= DATE('{AS_OF_DATE}')
              AND v.ENDDATE < DATE('{AS_OF_NEXT_DATE}')
              AND v.CREDIBILITYCODE = 1
              AND v.YIELDCODE = 2
              AND (v.INSERTTIME IS NULL OR v.INSERTTIME < DATE('{AS_OF_NEXT_DATE}'))
        ) ranked
        WHERE rn = 1 AND valuation_yield_pct IS NOT NULL
          AND remaining_years BETWEEN {MIN_REMAINING_YEARS} AND {MAX_REMAINING_YEARS};
        """,
        timeout=600,
    )
    exec_df(connector, conn, "ALTER TABLE tmp_b076_valuation ADD PRIMARY KEY (main_code)", timeout=120)

    exec_df(
        connector,
        conn,
        f"""
        CREATE TEMPORARY TABLE tmp_b076_rating AS
        SELECT main_code, rating_desc
        FROM (
            SELECT
                CAST(r.MAINCODE AS UNSIGNED) AS main_code,
                r.CRDESC AS rating_desc,
                ROW_NUMBER() OVER (
                    PARTITION BY r.MAINCODE
                    ORDER BY
                        COALESCE(r.INFOPUBLDATE, r.CRDATE) DESC,
                        r.CRDATE DESC,
                        r.ID DESC
                ) AS rn
            FROM bond_bdcreditgrading r FORCE INDEX (IX_BOND_BDCREDITGRADING_Q047)
            JOIN tmp_b076_valuation v
              ON v.main_code = r.MAINCODE
            WHERE COALESCE(r.INFOPUBLDATE, r.INSERTTIME) < DATE('{AS_OF_NEXT_DATE}')
              AND (r.CRDATE IS NULL OR r.CRDATE < DATE('{AS_OF_NEXT_DATE}'))
              AND r.CRTYPECODE = 2
              AND r.CRSYSTEM = 1
              AND r.CRDESC IS NOT NULL
              AND r.CRSTATUS = 1
              AND (r.RATINGEFFECTIVEENDDATE IS NULL OR r.RATINGEFFECTIVEENDDATE >= DATE('{AS_OF_DATE}'))
        ) ranked
        WHERE rn = 1
          AND rating_desc = 'AAA';
        """,
        timeout=300,
    )
    exec_df(connector, conn, "ALTER TABLE tmp_b076_rating ADD PRIMARY KEY (main_code)", timeout=120)

    exec_df(
        connector,
        conn,
        """
        CREATE TEMPORARY TABLE tmp_b076_display AS
        SELECT main_code, bond_code, bond_name, issuer
        FROM (
            SELECT
                cb.main_code,
                cb.bond_code,
                cb.bond_name,
                cb.issuer,
                ROW_NUMBER() OVER (
                    PARTITION BY cb.main_code
                    ORDER BY
                        CASE WHEN cb.secu_market = 89 THEN 0 ELSE 1 END,
                        CASE WHEN cb.inner_code = cb.main_code THEN 0 ELSE 1 END,
                        CASE WHEN cb.bond_code REGEXP '^[0-9]{6}$' THEN 0 ELSE 1 END,
                        COALESCE(cb.bond_code, '') ASC,
                        cb.inner_code ASC
                ) AS rn
            FROM tmp_b076_credit_bonds cb
            JOIN tmp_b076_valuation v
              ON v.main_code = cb.main_code
        ) ranked
        WHERE rn = 1;
        """,
        timeout=120,
    )
    exec_df(connector, conn, "ALTER TABLE tmp_b076_display ADD PRIMARY KEY (main_code)", timeout=120)


def format_result(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    result = df.copy()
    for col in ["剩余期限", "估值收益率"]:
        result[col] = pd.to_numeric(result[col], errors="coerce").map(lambda value: f"{value:.4f}" if pd.notna(value) else "")
    for col in ["当前套息空间", "情景套息空间"]:
        result[col] = pd.to_numeric(result[col], errors="coerce").map(lambda value: f"{value:.2f}" if pd.notna(value) else "")
    return result[RESULT_COLUMNS]


def main() -> None:
    args = parse_args()
    output_path = Path(args.output_path)
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")

    connector = get_db_connector()
    with connector.connection() as conn:
        create_temp_tables(connector, conn)
        result = exec_df(connector, conn, sql, timeout=300)

    result = format_result(result)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
