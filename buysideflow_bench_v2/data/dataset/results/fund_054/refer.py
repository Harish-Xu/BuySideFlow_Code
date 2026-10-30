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
    "预警层级",
    "基金代码",
    "基金简称",
    "披露日期",
    "核心机构标识",
    "期初持有份额(亿份)",
    "期末持有份额(亿份)",
    "份额下降额(亿份)",
    "下降比例(%)",
    "期末持有占比(%)",
]


def main() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    missing = [column for column in EXPECTED_COLUMNS if column not in df.columns]
    extra = [column for column in df.columns if column not in EXPECTED_COLUMNS]
    if missing or extra:
        raise ValueError(f"Unexpected result columns; missing={missing}, extra={extra}")
    df = df[EXPECTED_COLUMNS]
    df.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
