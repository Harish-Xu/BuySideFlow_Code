#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

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
    for column in ["年末估值收益率", "同组中位数收益率"]:
        df[column] = pd.to_numeric(df[column], errors="raise").map(lambda value: f"{value:.4f}")
    df["相对中位数利差"] = pd.to_numeric(
        df["相对中位数利差"], errors="raise"
    ).map(lambda value: f"{value:.2f}")
    return df


if __name__ == "__main__":
    finalize_run(main())
