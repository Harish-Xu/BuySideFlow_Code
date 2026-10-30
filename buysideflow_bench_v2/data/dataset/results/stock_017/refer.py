#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


EXPECTED_COLUMNS = ["代码", "名称", "所属行业", "PB", "营业收入同比增速", "总资产同比增速"]


def main() -> pd.DataFrame:
    from benchmark_paths import load_benchmark_env
    load_benchmark_env()
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    if list(df.columns) != EXPECTED_COLUMNS:
        raise ValueError(f"Unexpected output columns: {list(df.columns)}")
    df = df.copy()
    df["代码"] = df["代码"].astype(str).str.zfill(6)
    df["PB"] = pd.to_numeric(df["PB"], errors="raise").map(lambda value: f"{value:.4f}")
    for column in ["营业收入同比增速", "总资产同比增速"]:
        df[column] = pd.to_numeric(df[column], errors="raise").map(lambda value: f"{value:.2f}")
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
