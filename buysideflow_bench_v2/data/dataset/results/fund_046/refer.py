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
    "有效交易日数",
    "日胜率(%)",
    "日均超额收益(%)",
    "年化超额收益(%)",
    "跟踪误差(%)",
]


def main() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    missing_columns = [col for col in EXPECTED_COLUMNS if col not in df.columns]
    if missing_columns:
        raise ValueError(f"Missing expected columns: {missing_columns}")
    df = df[EXPECTED_COLUMNS].copy()
    df.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
