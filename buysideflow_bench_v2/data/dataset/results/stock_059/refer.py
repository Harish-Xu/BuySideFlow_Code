#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


RESULT_COLUMNS = ["行业", "代码", "报告期末日期", "其他应收占比", "其他应付占比"]


def main() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        raise ValueError("SQL execution returned no eligible companies")
    if list(df.columns) != RESULT_COLUMNS:
        raise ValueError(f"Unexpected result columns: {list(df.columns)}")
    for column in RESULT_COLUMNS[-2:]:
        df[column] = pd.to_numeric(df[column], errors="raise").map(lambda value: f"{value:.2f}")
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
