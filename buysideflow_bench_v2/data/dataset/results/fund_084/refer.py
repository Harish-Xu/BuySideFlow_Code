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
    df = query_to_dataframe(sql, max_rows=0, timeout=180)
    if df is None:
        raise ValueError("SQL execution returned None")
    df.columns = [str(column).lower() for column in df.columns]
    df = df.rename(
        columns={
            "fund_code": "基金代码",
            "fund_name": "基金简称",
            "status_end_date": "状态截止日",
            "large_applying_max": "大额申购上限（元）",
        }
    )
    output_columns = ["基金代码", "基金简称", "状态截止日", "大额申购上限（元）"]
    missing = [column for column in output_columns if column not in df.columns]
    if missing:
        raise ValueError(f"SQL result missing columns: {missing}")
    df = df[output_columns]
    df["状态截止日"] = pd.to_datetime(df["状态截止日"], errors="raise").dt.strftime("%Y-%m-%d")
    df["大额申购上限（元）"] = pd.to_numeric(df["大额申购上限（元）"], errors="raise")
    df.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(df.to_csv(index=False, float_format="%.2f").strip())
    return df


if __name__ == "__main__":
    main()
