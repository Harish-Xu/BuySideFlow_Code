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
    "期限",
    "上周五收益率(%)",
    "本周五收益率(%)",
    "周变动(bp)",
]
EXPECTED_TENORS = ["1Y", "2Y", "3Y", "5Y", "7Y", "10Y"]


def main():
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")

    missing = [col for col in EXPECTED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"missing expected columns: {missing}")

    df = df[EXPECTED_COLUMNS].copy()
    if df.empty:
        raise ValueError("bond_011 result is empty")
    if df["期限"].tolist() != EXPECTED_TENORS:
        raise ValueError(f"unexpected tenor list: {df['期限'].tolist()}")

    numeric_cols = ["上周五收益率(%)", "本周五收益率(%)", "周变动(bp)"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df[numeric_cols].isna().any().any():
        raise ValueError("result contains null or non-numeric yield values")

    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(df.to_string(index=False))
    return df


if __name__ == "__main__":
    main()
