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
    "首次违约日期",
    "首只违约债券简称",
    "发行人",
    "企业性质",
    "所属行业",
    "违约类型",
]


def validate(result: pd.DataFrame) -> None:
    if list(result.columns) != RESULT_COLUMNS:
        raise ValueError(f"Unexpected columns: {list(result.columns)}")
    if result.empty:
        raise ValueError("No subjects found for first default date in 2025")
    duplicate_subjects = result["发行人"].duplicated(keep=False)
    if duplicate_subjects.any():
        duplicated = result.loc[duplicate_subjects, "发行人"].tolist()
        raise ValueError(f"Duplicated issuer rows found: {duplicated}")


def main() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    result = query_to_dataframe(sql, max_rows=0, timeout=600)
    if result is None:
        raise RuntimeError("query_to_dataframe returned None")

    result = result.loc[:, RESULT_COLUMNS].fillna("")
    validate(result)

    output_path = Path(__file__).with_name("result.csv")
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
