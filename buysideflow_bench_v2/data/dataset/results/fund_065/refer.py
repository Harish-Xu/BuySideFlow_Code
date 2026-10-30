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
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    expected_columns = ["基金公司名称", "非货主基金数量", "平均设立规模（亿元）", "合计总资产（亿元）"]
    if len(df.columns) != len(expected_columns):
        raise ValueError(f"Expected {len(expected_columns)} output columns, got {len(df.columns)}")
    df.columns = expected_columns
    df["非货主基金数量"] = pd.to_numeric(df["非货主基金数量"], errors="raise").astype(int)
    for column in ["平均设立规模（亿元）", "合计总资产（亿元）"]:
        df[column] = pd.to_numeric(df[column], errors="coerce").map(
            lambda value: None if pd.isna(value) else f"{value:.2f}"
        )
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
