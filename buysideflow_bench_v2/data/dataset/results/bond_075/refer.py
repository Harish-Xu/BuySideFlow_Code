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
    "转债代码",
    "转债简称",
    "下修实施公告日",
    "收益基准日",
    "第5个交易日",
    "下修幅度",
    "事件窗口转债涨跌幅",
    "事件窗口正股涨跌幅",
]


def _validate_result(df: pd.DataFrame) -> None:
    if list(df.columns) != RESULT_COLUMNS:
        raise ValueError(f"Unexpected columns: {list(df.columns)}")
    if df.empty:
        return

    numeric_cols = [
        "下修幅度",
        "事件窗口转债涨跌幅",
        "事件窗口正股涨跌幅",
    ]
    if df[numeric_cols].isna().any().any():
        raise ValueError("Numeric result columns contain null values")
    if (df["下修幅度"] <= 0).any():
        raise ValueError("Cut ratio must be positive")

    announcement_dates = pd.to_datetime(df["下修实施公告日"], errors="coerce")
    base_dates = pd.to_datetime(df["收益基准日"], errors="coerce")
    fifth_dates = pd.to_datetime(df["第5个交易日"], errors="coerce")
    if announcement_dates.isna().any():
        raise ValueError("Announcement dates contain invalid values")
    if (announcement_dates < pd.Timestamp("2025-01-01")).any() or (
        announcement_dates > pd.Timestamp("2025-06-30")
    ).any():
        raise ValueError("Announcement dates fall outside 2025-01-01 through 2025-06-30")
    if base_dates.isna().any() or fifth_dates.isna().any():
        raise ValueError("Event-window dates contain invalid values")
    if not ((base_dates < announcement_dates) & (fifth_dates > announcement_dates)).all():
        raise ValueError("Event-window endpoints do not bracket the announcement date")
    if len(df) > 1 and (announcement_dates.diff().dt.days.fillna(0) > 0).any():
        raise ValueError("Result is not sorted by announcement date descending")


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
