#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


RESULT_COLUMNS = [
    "月份",
    "下调债券只数",
    "下调债券涉及发行人家数",
    "展望转负面发行人家数",
    "新列入下调观察发行人家数",
]


def main():
    base_dir = Path(__file__).resolve().parent
    sql = (base_dir / "refer.sql").read_text(encoding="utf-8")
    result = query_to_dataframe(sql, max_rows=0, timeout=300)
    if result is None or result.empty:
        raise RuntimeError("SQL returned no rows")

    missing = [column for column in RESULT_COLUMNS if column not in result.columns]
    if missing:
        raise RuntimeError(f"SQL result missing columns: {missing}")

    result = result[RESULT_COLUMNS].copy()
    for column in RESULT_COLUMNS[1:]:
        result[column] = result[column].astype(int)

    result.to_csv(base_dir / "result.csv", index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
