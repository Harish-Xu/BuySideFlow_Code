#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe
from sweagent.text2sql.task_output_helper import finalize_run


def main():
    sql_path = Path(__file__).with_name("refer.sql")
    sql = sql_path.read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    result = df.rename(
        columns={
            "issue_bond_count": "发行只数",
            "planned_issue_size_100million": "计划发行规模（亿元）",
            "net_financing_100million": "净融资额（亿元）",
        }
    )
    output_columns = ["发行只数", "计划发行规模（亿元）", "净融资额（亿元）"]
    missing = [column for column in output_columns if column not in result.columns]
    if missing:
        raise ValueError(f"SQL result missing columns: {missing}")
    return result[output_columns]


if __name__ == "__main__":
    finalize_run(main())
