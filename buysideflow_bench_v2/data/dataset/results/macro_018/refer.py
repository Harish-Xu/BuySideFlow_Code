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
    "项目",
    "年月",
    "PPI同比",
    "CPI同比",
    "剪刀差",
    "剪刀差排名",
    "剪刀差历史分位",
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
    df.to_csv(script_dir / "comparison_raw.csv", index=False, encoding="utf-8-sig")
    if not df.empty:
        for column in ["PPI同比", "CPI同比", "剪刀差"]:
            df[column] = pd.to_numeric(df[column], errors="raise").map(lambda value: f"{value:.1f}")
        df["剪刀差历史分位"] = pd.to_numeric(
            df["剪刀差历史分位"], errors="raise"
        ).map(lambda value: f"{value:.4f}")
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
