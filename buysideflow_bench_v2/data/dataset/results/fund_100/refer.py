#!/usr/bin/env python3
"""Return the five funds' year-to-date manager departure announcements.

The window applies to INFOPUBLDATE, not DIMISSIONDATE. An empty result keeps
the five output columns and contains no placeholder or explanatory data row.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


EXPECTED_COLUMNS = ["基金代码", "基金简称", "基金经理姓名", "离任日期", "公告日期"]


def main() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    if df.empty and len(df.columns) == 0:
        df = pd.DataFrame(columns=EXPECTED_COLUMNS)
    missing_columns = [col for col in EXPECTED_COLUMNS if col not in df.columns]
    if missing_columns:
        raise ValueError(f"Missing expected columns: {missing_columns}")
    df = df[EXPECTED_COLUMNS].copy()
    for col in ["离任日期", "公告日期"]:
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d")
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
