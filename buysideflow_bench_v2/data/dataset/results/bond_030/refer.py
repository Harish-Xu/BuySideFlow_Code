#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


RESULT_COLUMNS = [
    "评级机构",
    "覆盖只数",
    "下调只数",
    "下调率",
    "涉及发行人家数",
    "下调债券合计发行规模",
]


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    sql = (base_dir / "refer.sql").read_text(encoding="utf-8")
    result = query_to_dataframe(sql, max_rows=0, timeout=600)
    if result is None or result.empty:
        raise ValueError("SQL returned no rows")

    missing = [column for column in RESULT_COLUMNS if column not in result.columns]
    if missing:
        raise ValueError(f"SQL result missing columns: {missing}")

    result = result[RESULT_COLUMNS].copy()
    int_columns = ["覆盖只数", "下调只数", "涉及发行人家数"]
    for column in int_columns:
        result[column] = result[column].astype(int)
    for column in ["下调率", "下调债券合计发行规模"]:
        result[column] = result[column].astype(float).map(
            lambda value: None if pd.isna(value) else f"{value:.2f}"
        )

    result.to_csv(base_dir / "result.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
