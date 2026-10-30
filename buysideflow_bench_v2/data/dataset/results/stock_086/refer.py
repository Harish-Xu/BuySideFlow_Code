#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe
from benchmark_paths import load_benchmark_env
from sweagent.text2sql.provenance import require_db_snapshot_id


def main() -> pd.DataFrame:
    load_benchmark_env()
    if require_db_snapshot_id() != "jydb_20260430_v1":
        raise ValueError("This reference requires snapshot jydb_20260430_v1")
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        raise ValueError("Reference query returned no rows")
    df.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    print(df.to_string(index=False) if not df.empty else "<empty result>")
    return df


if __name__ == "__main__":
    main()
