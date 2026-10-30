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
    "区间起始季度",
    "区间结束季度",
    "季度",
    "GDP同比增速",
    "GDP季调环比增速",
]


def main() -> pd.DataFrame:
    script_dir = Path(__file__).resolve().parent
    sql = (script_dir / "refer.sql").read_text(encoding="utf-8")
    output_path = script_dir / "result.csv"
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    if df.empty and len(df.columns) == 0:
        df = pd.DataFrame(columns=EXPECTED_COLUMNS)

    missing = [col for col in EXPECTED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"missing expected columns: {missing}")

    df = df[EXPECTED_COLUMNS].copy()
    for col in ["GDP同比增速", "GDP季调环比增速"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df[["GDP同比增速", "GDP季调环比增速"]].isna().any().any():
        raise ValueError("GDP growth columns contain null or non-numeric values")
    if (df["GDP季调环比增速"] >= 1.0).any():
        raise ValueError("weak-growth filter failed")

    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
