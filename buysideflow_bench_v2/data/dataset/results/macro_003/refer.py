#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmark_paths import load_benchmark_env
from sweagent.text2sql.db_connector import query_to_dataframe
from sweagent.text2sql.provenance import require_db_snapshot_id

PERIODS = ['2025年以来', '2025年上半年', '2021年', '2022年', '2023年', '2024年']


def main() -> pd.DataFrame:
    load_benchmark_env()
    require_db_snapshot_id()
    script_dir = Path(__file__).resolve().parent
    sql = (script_dir / 'refer.sql').read_text(encoding='utf-8')
    output_path = script_dir / 'result.csv'
    df = query_to_dataframe(sql, max_rows=len(PERIODS), timeout=60)
    if df is None:
        raise ValueError("SQL execution returned None")
    if list(df.columns) != ['统计期', '失业率平均值'] or df['统计期'].tolist() != PERIODS:
        raise ValueError('Expected exactly the six requested periods and two output columns')
    if df['失业率平均值'].isna().any():
        raise ValueError('A required period lacks a complete set of valid, published monthly values')
    df.to_csv(output_path, index=False, encoding='utf-8-sig', float_format='%.4f')
    print(df.to_string(index=False))
    return df


if __name__ == "__main__":
    main()
