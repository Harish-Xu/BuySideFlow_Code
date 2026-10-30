#!/usr/bin/env python3
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "sweagent" / "text2sql" / "db_connector.py").exists():
            return parent
    raise RuntimeError("Cannot locate repository root")


REPO_ROOT = _repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import get_db_connector


OUTPUT_COLUMNS = [
    "债券代码",
    "债券简称",
    "期限段",
    "全年成交金额(亿元)",
    "成交天数",
]


def _execute(conn, sql: str) -> None:
    db = get_db_connector()
    ret = db.execute_query_on_connection(conn, sql, timeout=600)
    if isinstance(ret, dict) and not ret.get("success", False):
        raise RuntimeError(ret.get("error") or "SQL execution failed")


def _query(conn, sql: str) -> pd.DataFrame:
    db = get_db_connector()
    df = db.execute_sql_to_dataframe_on_connection(conn, sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    return df


def _build_result() -> pd.DataFrame:
    db = get_db_connector()
    with db.connection() as conn:
        _execute(
            conn,
            """
            CREATE TEMPORARY TABLE tmp_static_base AS
            SELECT DISTINCT
                bc.MAINCODE,
                bc.INNERCODE,
                bc.SECUCODE,
                bc.SECUABBR,
                bc.SECUMARKET,
                bc.LISTEDDATE,
                bc.DELISTDATE,
                bc.INTERESTENDDATE,
                bi.BONDFULLNAME,
                bi.RATEIFFLOAT,
                bi.OPMATURITY,
                bi.OPTIONTYPE
            FROM bond_code bc
            JOIN bond_basicinfo bi
              ON bi.MAINCODE = bc.MAINCODE
            WHERE bc.MAINCODE IS NOT NULL
              AND bc.INNERCODE IS NOT NULL
              AND bc.BONDTYPELEVEL1 = 1800
              AND (bc.LISTEDDATE IS NULL OR bc.LISTEDDATE <= '2024-12-31')
              AND (bc.INTERESTENDDATE IS NULL OR bc.INTERESTENDDATE > '2024-12-31')
              AND (bc.DELISTDATE IS NULL OR bc.DELISTDATE > '2024-12-31')
              AND (bi.ISSUEDATE IS NULL OR bi.ISSUEDATE <= '2024-12-31')
              AND (bi.INITIALINFOPUBLDATE IS NULL OR bi.INITIALINFOPUBLDATE <= '2024-12-31');
            """,
        )
        _execute(conn, "CREATE INDEX idx_tmp_static_base_main ON tmp_static_base(MAINCODE);")
        _execute(conn, "CREATE INDEX idx_tmp_static_base_inner ON tmp_static_base(INNERCODE, SECUMARKET);")

        # 固定利率、普通期限中票：剔除永续、次级、含回售/赎回/调票/续期/递延等期限不具可比性的品种。
        _execute(
            conn,
            """
            CREATE TEMPORARY TABLE tmp_candidate_codes AS
            SELECT *
            FROM tmp_static_base
            WHERE RATEIFFLOAT = 2
              AND OPMATURITY IS NULL
              AND COALESCE(OPTIONTYPE, '') NOT REGEXP '回售|赎回|调整票面利率|调票|续期|递延'
              AND CONCAT(COALESCE(SECUABBR, ''), COALESCE(BONDFULLNAME, '')) NOT REGEXP '永续|次级|二级资本';
            """,
        )
        _execute(conn, "CREATE INDEX idx_tmp_candidate_main ON tmp_candidate_codes(MAINCODE);")
        _execute(conn, "CREATE INDEX idx_tmp_candidate_inner ON tmp_candidate_codes(INNERCODE, SECUMARKET);")

        # 年末中债推荐估值给出剩余期限；本题不需要收益率，但用推荐估值记录确认年末期限。
        _execute(
            conn,
            """
            CREATE TEMPORARY TABLE tmp_year_end_val_ranked AS
            SELECT
                c.MAINCODE,
                c.INNERCODE,
                c.SECUCODE,
                c.SECUABBR,
                c.SECUMARKET,
                v.TRUEREMAINMATURITY,
                ROW_NUMBER() OVER (
                    PARTITION BY c.MAINCODE
                    ORDER BY
                        CASE c.SECUMARKET WHEN 89 THEN 1 WHEN 83 THEN 2 WHEN 90 THEN 3 ELSE 9 END,
                        c.SECUCODE,
                        c.INNERCODE,
                        v.UPDATETIME DESC,
                        v.INSERTTIME DESC,
                        v.ID DESC
                ) AS rn
            FROM tmp_candidate_codes c
            JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL)
              ON v.INNERCODE = c.INNERCODE
             AND v.ENDDATE = '2024-12-31'
             AND v.CREDIBILITYCODE = 1
             AND v.INSERTTIME < '2025-01-01'
            WHERE v.YIELDCODE = 2
              AND v.TRUEREMAINMATURITY >= 1
              AND v.TRUEREMAINMATURITY <= 5;
            """,
        )
        _execute(
            conn,
            """
            CREATE TEMPORARY TABLE tmp_year_end_val AS
            SELECT
                MAINCODE,
                INNERCODE,
                SECUCODE,
                SECUABBR,
                SECUMARKET,
                TRUEREMAINMATURITY,
                CASE
                    WHEN TRUEREMAINMATURITY >= 1 AND TRUEREMAINMATURITY < 3 THEN '1-3年'
                    WHEN TRUEREMAINMATURITY >= 3 AND TRUEREMAINMATURITY <= 5 THEN '3-5年'
                END AS term_bucket
            FROM tmp_year_end_val_ranked
            WHERE rn = 1;
            """,
        )
        _execute(conn, "CREATE INDEX idx_tmp_year_end_val_main ON tmp_year_end_val(MAINCODE);")

        _execute(
            conn,
            """
            CREATE TEMPORARY TABLE tmp_rating_ranked AS
            SELECT
                yv.MAINCODE,
                r.CRDESC,
                r.CRSTATUS,
                ROW_NUMBER() OVER (
                    PARTITION BY yv.MAINCODE
                    ORDER BY COALESCE(r.CRDATE, r.INFOPUBLDATE) DESC, r.INFOPUBLDATE DESC, r.ID DESC
                ) AS rn
            FROM tmp_year_end_val yv
            JOIN bond_bdcreditgrading r FORCE INDEX (IX_BOND_BDCREDITGRADING_Q047)
              ON r.MAINCODE = yv.MAINCODE
             AND r.INFOPUBLDATE < '2025-01-01'
             AND (r.CRDATE IS NULL OR r.CRDATE < '2025-01-01')
             AND r.CRTYPECODE = 2
             AND r.CRSYSTEM = 1
             AND r.CRDESC IS NOT NULL
             AND (
                 r.RATINGEFFECTIVEENDDATE IS NULL
                 OR r.RATINGEFFECTIVEENDDATE >= '2024-12-31'
             );
            """,
        )
        _execute(
            conn,
            """
            CREATE TEMPORARY TABLE tmp_year_end_scope AS
            SELECT
                yv.MAINCODE,
                yv.SECUCODE,
                yv.SECUABBR,
                rr.CRDESC AS rating,
                yv.term_bucket,
                yv.TRUEREMAINMATURITY
            FROM tmp_year_end_val yv
            JOIN tmp_rating_ranked rr
              ON rr.MAINCODE = yv.MAINCODE
             AND rr.rn = 1
            WHERE rr.CRDESC = 'AAA'
              AND rr.CRSTATUS = 1
              AND yv.term_bucket IS NOT NULL;
            """,
        )
        _execute(conn, "CREATE INDEX idx_tmp_year_end_scope_main ON tmp_year_end_scope(MAINCODE);")

        _execute(
            conn,
            """
            CREATE TEMPORARY TABLE tmp_trade_daily AS
            SELECT
                s.MAINCODE,
                DATE(q.TRADINGDAY) AS trade_date,
                SUM(q.TURNOVERVALUE) AS daily_turnover_yuan
            FROM tmp_year_end_scope s
            JOIN bond_code c
              ON c.MAINCODE = s.MAINCODE
            JOIN bond_basicquote q FORCE INDEX (IX_BOND_BASICQUOTE)
              ON q.INNERCODE = c.INNERCODE
             AND q.SECUMARKET = c.SECUMARKET
             AND q.TRADINGDAY >= '2024-01-01'
             AND q.TRADINGDAY < '2025-01-01'
            WHERE q.TURNOVERVALUE IS NOT NULL
              AND q.TURNOVERVALUE > 0
              AND (c.LISTEDDATE IS NULL OR c.LISTEDDATE <= q.TRADINGDAY)
              AND (c.INTERESTENDDATE IS NULL OR c.INTERESTENDDATE > q.TRADINGDAY)
              AND (c.DELISTDATE IS NULL OR c.DELISTDATE > q.TRADINGDAY)
            GROUP BY s.MAINCODE, DATE(q.TRADINGDAY);
            """,
        )
        _execute(conn, "CREATE INDEX idx_tmp_trade_daily_main ON tmp_trade_daily(MAINCODE);")

        _execute(
            conn,
            """
            CREATE TEMPORARY TABLE tmp_trade_summary AS
            SELECT
                MAINCODE,
                SUM(daily_turnover_yuan) AS annual_turnover_yuan,
                COUNT(*) AS trade_days
            FROM tmp_trade_daily
            GROUP BY MAINCODE;
            """,
        )
        _execute(conn, "CREATE INDEX idx_tmp_trade_summary_main ON tmp_trade_summary(MAINCODE);")

        _execute(
            conn,
            """
            CREATE TEMPORARY TABLE tmp_ranked_result AS
            SELECT
                s.SECUCODE AS bond_code,
                s.SECUABBR AS bond_abbr,
                s.term_bucket,
                COALESCE(ts.annual_turnover_yuan, 0) AS annual_turnover_yuan,
                COALESCE(ts.trade_days, 0) AS trade_days,
                ROW_NUMBER() OVER (
                    PARTITION BY s.term_bucket
                    ORDER BY COALESCE(ts.annual_turnover_yuan, 0) DESC, s.SECUCODE
                ) AS group_rank
            FROM tmp_year_end_scope s
            LEFT JOIN tmp_trade_summary ts
              ON ts.MAINCODE = s.MAINCODE;
            """,
        )

        result = _query(
            conn,
            """
            SELECT
                bond_code AS `债券代码`,
                bond_abbr AS `债券简称`,
                term_bucket AS `期限段`,
                ROUND(annual_turnover_yuan / 100000000, 2) AS `全年成交金额(亿元)`,
                trade_days AS `成交天数`
            FROM tmp_ranked_result
            WHERE group_rank <= 10
            ORDER BY term_bucket, annual_turnover_yuan DESC, bond_code;
            """,
        )

    return result[OUTPUT_COLUMNS]


def main() -> pd.DataFrame:
    started = time.time()
    result = _build_result()
    if not result.empty:
        result["债券代码"] = result["债券代码"].astype(str)
        result["成交天数"] = pd.to_numeric(result["成交天数"], errors="coerce").fillna(0).astype(int)
    result_dir = Path(__file__).resolve().parent
    result.to_csv(result_dir / "result.csv", index=False, encoding="utf-8-sig")
    print(f"# elapsed_seconds={time.time() - started:.2f}", file=sys.stderr)
    return result


if __name__ == "__main__":
    print(main().to_csv(index=False), end="")
