#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "sweagent").exists() and (parent / "data").exists():
            return parent
    raise RuntimeError("Cannot locate repository root")


REPO_ROOT = _repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


RESULT_COLUMNS = [
    "债券代码",
    "债券简称",
    "发行人",
    "重仓基金只数",
    "合计持仓市值",
    "存量规模",
    "重仓集中度",
]


def _validate_result(df: pd.DataFrame) -> None:
    if list(df.columns) != RESULT_COLUMNS:
        raise ValueError(f"Unexpected columns: {list(df.columns)}")
    if len(df) > 20:
        raise ValueError(f"Expected at most 20 rows, got {len(df)}")
    if df["债券代码"].duplicated().any():
        duplicated = df.loc[df["债券代码"].duplicated(), "债券代码"].tolist()
        raise ValueError(f"Duplicate bond codes in result: {duplicated}")
    numeric_cols = ["重仓基金只数", "合计持仓市值", "存量规模", "重仓集中度"]
    if df[numeric_cols].isna().any().any():
        raise ValueError("Numeric result columns contain null values")
    if (df["重仓基金只数"] < 3).any():
        raise ValueError("Result contains bonds held by fewer than 3 funds")
    if (df["合计持仓市值"] <= 0).any() or (df["存量规模"] <= 0).any():
        raise ValueError("Market value and outstanding size must be positive")
    if len(df) > 1 and (df["重仓集中度"].diff().fillna(0) > 0).any():
        raise ValueError("Displayed concentration is not non-increasing")


def main() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    df = df[RESULT_COLUMNS]
    _validate_result(df)
    output_path = Path(__file__).with_name("result.csv")
    df.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    return df


if __name__ == "__main__":
    main()
