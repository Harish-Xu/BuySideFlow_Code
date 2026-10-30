#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe
from sweagent.text2sql.task_output_helper import finalize_run


TARGET_FUND_CODES = ["090013", "014329", "006608", "003501", "000480", "003822", "004703"]
MATRIX_FUND_COLUMNS = sorted(TARGET_FUND_CODES)
RESULT_COLUMNS = ["报告期", "基金代码", *MATRIX_FUND_COLUMNS]
AS_OF_DATE = "2025-07-21"

HOLDING_SQL = f"""
WITH params AS (
    SELECT DATE('{AS_OF_DATE}') AS as_of_date
),
target_funds AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code
    FROM mf_fundarchives fa
    WHERE fa.SECURITYCODE IN ('090013', '014329', '006608', '003501', '000480', '003822', '004703')
),
common_report AS (
    SELECT h.REPORTDATE
    FROM (
        SELECT
            sp.REPORTDATE,
            COUNT(DISTINCT sp.INNERCODE) AS fund_count
        FROM mf_stockportfoliodetail sp
        JOIN target_funds tf
          ON tf.INNERCODE = sp.INNERCODE
        JOIN params p
          ON sp.REPORTDATE <= p.as_of_date
         AND sp.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE DATE_FORMAT(sp.REPORTDATE, '%m-%d') IN ('06-30', '12-31')
        GROUP BY sp.REPORTDATE
    ) h
    CROSS JOIN (
        SELECT COUNT(DISTINCT fund_code) AS target_count
        FROM target_funds
    ) t
    WHERE h.fund_count = t.target_count
    ORDER BY h.REPORTDATE DESC
    LIMIT 1
),
holding_ranked AS (
    SELECT
        tf.fund_code,
        DATE(sp.REPORTDATE) AS report_date,
        sp.STOCKINNERCODE,
        sp.RATIOINNV,
        ROW_NUMBER() OVER (
            PARTITION BY tf.fund_code, DATE(sp.REPORTDATE), sp.STOCKINNERCODE
            ORDER BY sp.INFOPUBLDATE DESC, sp.INSERTTIME DESC, sp.ID DESC
        ) AS rn
    FROM mf_stockportfoliodetail sp
    JOIN target_funds tf
      ON tf.INNERCODE = sp.INNERCODE
    JOIN common_report cr
      ON cr.REPORTDATE = sp.REPORTDATE
    JOIN params p
      ON sp.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE sp.STOCKINNERCODE IS NOT NULL
),
holding_agg AS (
    SELECT
        fund_code,
        report_date,
        STOCKINNERCODE,
        RATIOINNV AS position_weight
    FROM holding_ranked
    WHERE rn = 1
)
SELECT
    fund_code,
    report_date,
    STOCKINNERCODE AS stock_inner_code,
    position_weight
FROM holding_agg
ORDER BY fund_code, position_weight DESC, stock_inner_code
"""


def load_holdings() -> pd.DataFrame:
    df = query_to_dataframe(HOLDING_SQL, max_rows=0, timeout=300)
    if df is None or df.empty:
        raise ValueError("未取到目标基金共同可得报告期的股票持仓。")

    df = df.copy()
    df["fund_code"] = df["fund_code"].astype(str).str.zfill(6)
    df["report_date"] = pd.to_datetime(df["report_date"], errors="coerce")
    df["stock_inner_code"] = pd.to_numeric(df["stock_inner_code"], errors="coerce")
    df["position_weight"] = pd.to_numeric(df["position_weight"], errors="coerce")
    if df[["report_date", "stock_inner_code", "position_weight"]].isna().any().any():
        raise ValueError("最新持仓版本存在缺失字段，不能把缺失权重当作零或回退旧版本")

    observed_codes = set(df["fund_code"].unique())
    missing_codes = sorted(set(TARGET_FUND_CODES) - observed_codes)
    if missing_codes:
        raise ValueError(f"共同报告期缺少目标基金持仓: {missing_codes}")

    report_dates = df["report_date"].dt.strftime("%Y-%m-%d").unique()
    if len(report_dates) != 1:
        raise ValueError(f"expected exactly one common report date, got {report_dates}")
    return df


def build_similarity_matrix(holdings: pd.DataFrame) -> pd.DataFrame:
    weight_matrix_df = holdings.pivot_table(
        index="fund_code",
        columns="stock_inner_code",
        values="position_weight",
        aggfunc="sum",
        fill_value=0.0,
    ).reindex(index=MATRIX_FUND_COLUMNS, fill_value=0.0)

    weight_matrix = weight_matrix_df.to_numpy(dtype=float)
    norms = np.linalg.norm(weight_matrix, axis=1, keepdims=True)
    if np.any(norms == 0):
        zero_codes = weight_matrix_df.index[(norms.ravel() == 0)].tolist()
        raise ValueError(f"fund holding vector is empty: {zero_codes}")

    normalized_matrix = weight_matrix / norms
    similarity = normalized_matrix @ normalized_matrix.T
    similarity_df = pd.DataFrame(
        similarity,
        index=MATRIX_FUND_COLUMNS,
        columns=MATRIX_FUND_COLUMNS,
    ).round(4)

    result = similarity_df.reset_index().rename(columns={"index": "基金代码"})
    result.insert(0, "报告期", holdings["report_date"].max().strftime("%Y-%m-%d"))
    return result[RESULT_COLUMNS]


def main() -> pd.DataFrame:
    result = build_similarity_matrix(load_holdings())

    numeric = result[MATRIX_FUND_COLUMNS].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        raise ValueError("similarity matrix contains null or non-numeric values")
    if ((numeric < -1.0001) | (numeric > 1.0001)).any().any():
        raise ValueError("similarity value out of range")
    if not np.allclose(numeric.to_numpy(), numeric.to_numpy().T, atol=1e-4):
        raise ValueError("similarity matrix is not symmetric")
    if not np.allclose(np.diag(numeric.to_numpy()), 1.0, atol=1e-4):
        raise ValueError("similarity matrix diagonal should be 1")
    return result


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent)
    finalize_run(main())
