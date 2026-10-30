#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


EXPECTED_COLUMNS = ["权重日期", "行业名称", "行业占比"]


def main() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")

    missing = [col for col in EXPECTED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"missing expected columns: {missing}")

    df = df[EXPECTED_COLUMNS].copy()
    if df.empty:
        raise ValueError("stock_069 result is empty")

    df["权重日期"] = pd.to_datetime(df["权重日期"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["行业占比"] = pd.to_numeric(df["行业占比"], errors="coerce")
    if df["权重日期"].isna().any() or df["行业占比"].isna().any():
        raise ValueError("result contains invalid date or weight values")
    if not df["行业占比"].between(0, 100).all():
        raise ValueError("industry weights should be percentages between 0 and 100")
    if (df.groupby("权重日期").size() > 3).any():
        raise ValueError("each weight date should output at most 3 industries")

    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
