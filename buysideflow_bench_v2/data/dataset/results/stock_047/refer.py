#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


EXPECTED_COLUMNS = ["股票代码", "股票简称", "PEG", "ROE", "归母净利润三年复合增速", "资产负债率", "行业"]


def main() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    if list(df.columns) != EXPECTED_COLUMNS:
        raise ValueError(f"Unexpected output columns: {list(df.columns)}")
    df = df.copy()
    df["股票代码"] = df["股票代码"].astype(str).str.zfill(6)
    df["PEG"] = pd.to_numeric(df["PEG"], errors="raise").map(lambda value: f"{value:.4f}")
    for column in ["ROE", "归母净利润三年复合增速", "资产负债率"]:
        df[column] = pd.to_numeric(df[column], errors="raise").map(lambda value: f"{value:.4f}")
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
