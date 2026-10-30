#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


def format_decimal(value: float, digits: int) -> str:
    if pd.isna(value):
        return ""
    value = float(value)
    if abs(value) < 0.5 * 10 ** (-digits):
        value = 0.0
    return f"{value:.{digits}f}"


def main() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")

    expected_columns = [
        "股票代码",
        "股票简称",
        "PE",
        "股息率",
        "ROE",
        "营业收入同比增速",
        "归母净利润同比增速",
    ]
    df = df.reindex(columns=expected_columns)
    df["股票代码"] = df["股票代码"].astype(str).str.zfill(6)

    numeric_digits = {
        "PE": 2,
        "股息率": 2,
        "ROE": 2,
        "营业收入同比增速": 2,
        "归母净利润同比增速": 2,
    }
    for col in numeric_digits:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values(["股息率", "股票代码"], ascending=[False, True]).reset_index(drop=True)
    for col, digits in numeric_digits.items():
        df[col] = df[col].map(lambda x, d=digits: format_decimal(x, d))

    df = df.rename(
        columns={
            "股票简称": "简称",
            "营业收入同比增速": "营收同比增速",
        }
    )

    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
