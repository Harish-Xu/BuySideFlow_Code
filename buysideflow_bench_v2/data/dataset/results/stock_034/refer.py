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


RESULT_COLUMNS = ['指数简称', '近5日日均成交额', '此前20日日均成交额', '成交额变化率', '近5日涨跌幅']


def main() -> pd.DataFrame:
    load_benchmark_env()
    require_db_snapshot_id()
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=3, timeout=60)
    if df is None:
        raise ValueError("SQL execution returned None")
    if list(df.columns) != RESULT_COLUMNS or len(df) != 3 or set(df['指数简称']) != {'沪深300', '中证500', '中证1000'}:
        raise ValueError('Expected exactly the three requested indices and five output columns')
    df.to_csv(output_path, index=False, encoding="utf-8-sig", float_format='%.2f', na_rep='')
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
