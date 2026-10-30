#!/usr/bin/env python3
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmark_paths import load_benchmark_env
from sweagent.text2sql.db_connector import query_to_dataframe
from sweagent.text2sql.provenance import require_db_snapshot_id

COLUMNS = ["股票代码","证券简称","总市值","总资产","简化Q","加权ROE","固定资产周转率"]
DIGITS = {"总市值":2,"总资产":2,"简化Q":4,"加权ROE":2,"固定资产周转率":4}


def format_result(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=COLUMNS)
    if list(frame.columns) != COLUMNS or frame.isna().any().any():
        raise ValueError("Reference returned an invalid output schema or a missing value")
    result = frame.copy()
    result["股票代码"] = result["股票代码"].astype(str).str.zfill(6)
    # SQL has already ordered the original metrics; display rounding must not reorder rows.
    for column, digits in DIGITS.items():
        quantum = Decimal(1).scaleb(-digits)
        result[column] = result[column].map(
            lambda value, q=quantum, d=digits: format(
                Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP) + Decimal(0), f".{d}f"
            )
        )
    return result


def main() -> pd.DataFrame:
    load_benchmark_env()
    if require_db_snapshot_id() != "jydb_20260430_v1":
        raise ValueError("This reference regeneration requires jydb_20260430_v1")
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    frame = query_to_dataframe(sql, max_rows=0, timeout=600)
    if frame is None:
        raise ValueError("SQL execution returned None")
    result = format_result(frame)
    result.to_csv(Path(__file__).with_name("result.csv"), index=False, encoding="utf-8-sig")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    main()
