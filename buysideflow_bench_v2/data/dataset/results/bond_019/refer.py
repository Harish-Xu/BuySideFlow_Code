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
    "日期",
    "10Y变动(bp)",
    "FR007变动(bp)",
    "背离幅度(bp)",
    "触发天数",
    "最大背离日",
    "最大背离幅度(bp)",
]


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def format_result(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return empty_result()

    result = df.copy()
    result["日期"] = pd.to_datetime(result["日期"], errors="coerce").dt.strftime("%Y-%m-%d")
    result["最大背离日"] = pd.to_datetime(result["最大背离日"], errors="coerce").dt.strftime("%Y-%m-%d")
    for col in ["10Y变动(bp)", "FR007变动(bp)", "背离幅度(bp)", "最大背离幅度(bp)"]:
        result[col] = pd.to_numeric(result[col], errors="coerce").map(
            lambda x: "" if pd.isna(x) else f"{float(x):.2f}"
        )
    result["触发天数"] = pd.to_numeric(result["触发天数"], errors="coerce").astype("Int64")
    return result[RESULT_COLUMNS]


def main() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    result = format_result(df)
    output_path = Path(__file__).with_name("result.csv")
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
