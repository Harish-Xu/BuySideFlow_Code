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
    "指数代码",
    "指数简称",
    "年末规模(亿元)",
    "年末份额",
    "规模环比",
    "份额环比",
]


def main() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or list(df.columns) != EXPECTED_COLUMNS or len(df) != 2 or df.isna().any().any():
        raise ValueError("Both indices require complete quarter-end scale/share coverage and nonzero prior totals")
    df.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}") if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
