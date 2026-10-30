#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


RESULT_COLUMNS = [
    "转债代码",
    "转债简称",
    "正股代码",
    "当日触发价",
    "观察窗口",
    "要求天数",
    "已满足天数",
    "还差天数",
    "剩余债券余额",
]


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def main() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    if df.empty:
        return empty_result()

    df = df.rename(columns={"剩余债券余额(亿元)": "剩余债券余额"})

    missing = [col for col in RESULT_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"missing expected columns: {missing}")

    df = df[RESULT_COLUMNS].copy()
    numeric_cols = [
        "当日触发价",
        "观察窗口",
        "要求天数",
        "已满足天数",
        "还差天数",
        "剩余债券余额",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    if df[numeric_cols].isna().any().any():
        raise ValueError("numeric result contains null or non-numeric values")
    if (df["当日触发价"] <= 0).any():
        raise ValueError("trigger price must be positive")
    if (df["观察窗口"] <= 0).any() or (df["要求天数"] <= 0).any():
        raise ValueError("window and required days must be positive")
    if (df["已满足天数"] < 0).any() or (df["还差天数"] < 0).any():
        raise ValueError("progress day counts must be non-negative")
    if (df["已满足天数"] > df["观察窗口"]).any():
        raise ValueError("reached days cannot exceed observation window")
    if (df["还差天数"] != (df["要求天数"] - df["已满足天数"]).clip(lower=0)).any():
        raise ValueError("remaining days formula failed")
    if (df["还差天数"] > 5).any():
        raise ValueError("result should only include bonds with remaining days <= 5")

    int_cols = ["观察窗口", "要求天数", "已满足天数", "还差天数"]
    for col in int_cols:
        df[col] = df[col].astype(int)
    for col in ["当日触发价", "剩余债券余额"]:
        df[col] = df[col].round(2)

    return df


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent)
    result = main()
    result.to_csv("result.csv", index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_string(index=False))
