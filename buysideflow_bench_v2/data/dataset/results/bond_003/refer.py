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
    output_columns = [
        "分组",
        "纳入PMI统计的月份数",
        "PMI低于50的月份数",
        "PMI低于50的月份占比",
    ]
    missing = [column for column in output_columns if column not in df.columns]
    if missing:
        raise ValueError(f"SQL result missing columns: {missing}")
    df = df[output_columns].copy()
    df["纳入PMI统计的月份数"] = df["纳入PMI统计的月份数"].astype(int)
    df["PMI低于50的月份数"] = df["PMI低于50的月份数"].astype(int)
    df["PMI低于50的月份占比"] = df["PMI低于50的月份占比"].astype(float).map(
        lambda value: f"{value:.4f}"
    )
    return df


if __name__ == "__main__":
    finalize_run(main())
