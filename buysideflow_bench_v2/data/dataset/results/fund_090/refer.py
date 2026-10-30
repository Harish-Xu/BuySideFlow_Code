#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


def check_q4_coverage(sql: str) -> None:
    # Reuse the same population and publication cutoff as the result query.
    ctes = sql.rsplit("\nSELECT\n", 1)[0]
    missing = query_to_dataframe(
        ctes + "\nSELECT INNERCODE FROM q4_coverage WHERE coverage = 'unknown'",
        max_rows=0,
        timeout=600,
    )
    if missing is None or not missing.empty:
        raise ValueError("Q4 holdings coverage is unresolved; absence must not be treated as zero stocks")


def check_exit_weights(sql: str) -> None:
    # Keep stock identities/counts intact; only exited Q3 weights enter the sum.
    ctes = sql.rsplit("\nSELECT\n", 1)[0]
    missing = query_to_dataframe(
        ctes + """
SELECT DISTINCT q3.INNERCODE
FROM q3_holdings q3
LEFT JOIN q4_holdings q4
  ON q4.INNERCODE = q3.INNERCODE
 AND q4.STOCKINNERCODE = q3.STOCKINNERCODE
WHERE q4.STOCKINNERCODE IS NULL AND q3.ratio_in_nv IS NULL
""",
        max_rows=0,
        timeout=600,
    )
    if missing is None or not missing.empty:
        raise ValueError("Exited stock weights are incomplete; unknown weights must not be treated as zero")


def main() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    check_q4_coverage(sql)
    check_exit_weights(sql)
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    expected_columns = ["基金代码", "简称", "退出前十大股票数量", "退出股票三季报占净值比例合计"]
    if len(df.columns) != len(expected_columns):
        raise ValueError(f"Expected {len(expected_columns)} output columns, got {len(df.columns)}")
    df.columns = expected_columns
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
