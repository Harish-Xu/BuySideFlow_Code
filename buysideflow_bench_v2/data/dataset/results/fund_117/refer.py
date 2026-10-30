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
    df = df.rename(
        columns={
            "合计跟踪规模（亿元）": "合计跟踪规模",
            "最大 ETF 规模（亿元）": "最大 ETF 规模",
        }
    )
    output_columns = [
        "指数代码",
        "指数简称",
        "跟踪 ETF 数量",
        "合计跟踪规模",
        "最大 ETF 名称",
        "最大 ETF 规模",
    ]
    missing = [column for column in output_columns if column not in df.columns]
    if missing:
        raise ValueError(f"SQL result missing columns: {missing}")
    df = df[output_columns]
    df.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}") if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
