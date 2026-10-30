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
    if df is None or df.empty:
        raise ValueError("Reference query returned no rows")

    percent_cols = [
        "平均ROE(%)",
        "5日平均收益率(%)",
        "10日平均收益率(%)",
        "20日平均收益率(%)",
    ]
    for col in percent_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(2)

    df.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
