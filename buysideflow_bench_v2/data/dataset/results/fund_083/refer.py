#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


def main() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    df = df.rename(
        columns={
            "fund_code": "基金代码",
            "fund_name": "基金简称",
            "latest_total_asset_yi": "最新资产总值",
            "asset_decline_pct": "资产总值下降比例",
            "individual_ratio_increase_pp": "个人投资者持有占比提升幅度",
        }
    )
    output_columns = [
        "基金代码",
        "基金简称",
        "最新资产总值",
        "资产总值下降比例",
        "个人投资者持有占比提升幅度",
    ]
    missing = [column for column in output_columns if column not in df.columns]
    if missing:
        raise ValueError(f"SQL result missing columns: {missing}")
    df = df[output_columns].copy()
    df["基金代码"] = df["基金代码"].astype(str).str.zfill(6)
    for column in ["最新资产总值", "资产总值下降比例", "个人投资者持有占比提升幅度"]:
        df[column] = pd.to_numeric(df[column], errors="raise").map(lambda value: f"{value:.2f}")
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
