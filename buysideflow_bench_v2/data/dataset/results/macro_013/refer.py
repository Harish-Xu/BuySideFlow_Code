#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


OUTPUT_COLUMNS = ["月份", "GDP同比", "CPI同比"]


def main() -> pd.DataFrame:
    script_dir = Path(__file__).resolve().parent
    sql = (script_dir / "refer.sql").read_text(encoding="utf-8")
    output_path = script_dir / "result.csv"
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    if df.empty:
        df = pd.DataFrame(columns=OUTPUT_COLUMNS)
    if list(df.columns) != OUTPUT_COLUMNS:
        raise ValueError(f"Unexpected output columns: {list(df.columns)}")
    if df.isna().any().any():
        raise ValueError("SQL execution returned missing output values")

    csv_df = df.copy()
    csv_df["GDP同比"] = csv_df["GDP同比"].map(lambda value: f"{value:.1f}")
    csv_df["CPI同比"] = csv_df["CPI同比"].map(lambda value: f"{value:.1f}")
    csv_df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
