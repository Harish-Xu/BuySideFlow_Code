#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


RESULT_COLUMNS = [
    "周起始日",
    "债券品种代码",
    "当周发行规模(亿元)",
    "前4周平均发行规模(亿元)",
    "增幅",
]


def format_code(value) -> str:
    if pd.isna(value):
        return ""
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.notna(numeric) and float(numeric).is_integer():
        return str(int(numeric))
    return str(value)


def main() -> pd.DataFrame:
    base_dir = Path(__file__).resolve().parent
    sql = (base_dir / "refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    if list(df.columns) != RESULT_COLUMNS:
        raise ValueError(f"Unexpected columns: {list(df.columns)}")

    result = df.copy()
    result["周起始日"] = pd.to_datetime(result["周起始日"]).dt.strftime("%Y-%m-%d")
    result["债券品种代码"] = result["债券品种代码"].map(format_code)
    for col in ["当周发行规模(亿元)", "前4周平均发行规模(亿元)", "增幅"]:
        result[col] = pd.to_numeric(result[col], errors="raise").map(lambda x: f"{x:.2f}")
    result.to_csv(base_dir / "result.csv", index=False, encoding="utf-8-sig")
    return result


if __name__ == "__main__":
    main()
