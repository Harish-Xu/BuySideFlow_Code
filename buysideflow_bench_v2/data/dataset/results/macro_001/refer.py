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
    script_dir = Path(__file__).resolve().parent
    sql = (script_dir / "refer.sql").read_text(encoding="utf-8")
    output_path = script_dir / "result.csv"
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    expected = ["统计期末日期", "GDP同比增速"]
    if list(df.columns) != expected or len(df) != 3:
        raise ValueError(f"Unexpected GDP comparison result: columns={list(df.columns)}, rows={len(df)}")
    df = df.copy()
    df["统计期末日期"] = pd.to_datetime(df["统计期末日期"]).dt.strftime("%Y-%m-%d")
    df["GDP同比增速"] = pd.to_numeric(df["GDP同比增速"], errors="raise").map(
        lambda value: f"{value:.2f}"
    )
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
