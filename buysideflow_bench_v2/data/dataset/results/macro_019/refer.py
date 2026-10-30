#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


EXPECTED_COLUMNS = [
    "近6期负增长月数",
    "近6期最长连续月数",
    "历史最长区间",
    "历史持续月数",
    "近6期M2同比均值",
    "近6期社融增量同比均值",
    "历史区间M2同比均值",
    "历史区间社融增量同比均值",
]

MEAN_COLUMNS = [
    "近6期M2同比均值",
    "近6期社融增量同比均值",
    "历史区间M2同比均值",
    "历史区间社融增量同比均值",
]


def main() -> pd.DataFrame:
    script_dir = Path(__file__).resolve().parent
    sql = (script_dir / "refer.sql").read_text(encoding="utf-8")
    output_path = script_dir / "result.csv"
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    if df.empty:
        raise ValueError("SQL execution returned no historical CPI contraction interval")
    if list(df.columns) != EXPECTED_COLUMNS:
        raise ValueError(f"Unexpected result columns: {list(df.columns)}")

    csv_df = df.copy()
    for column in MEAN_COLUMNS:
        csv_df[column] = csv_df[column].map(
            lambda value: "" if pd.isna(value) else f"{float(value):.1f}"
        )
    csv_df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
