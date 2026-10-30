#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


def main() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    summary_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")

    summary_df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if summary_df is None:
        raise ValueError("Summary SQL execution returned None")

    summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(summary_df.to_string(index=False) if not summary_df.empty else "<empty summary>")
    return summary_df


if __name__ == "__main__":
    main()
