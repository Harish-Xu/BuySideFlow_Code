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
    "指数名称",
    "公司季度样本数",
    "单季ROE均值",
    "单季ROE最小值",
    "单季经营现金流率均值",
    "单季经营现金流率最小值",
]


def main() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    if list(df.columns) != EXPECTED_COLUMNS:
        raise ValueError(f"Unexpected output columns: {list(df.columns)}")
    if len(df) != 2:
        raise ValueError(f"Expected two index rows, got {len(df)}")
    df.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
