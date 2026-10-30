#!/usr/bin/env python3
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "sweagent" / "text2sql" / "db_connector.py").exists():
            return parent
    raise RuntimeError("Cannot locate repository root")


REPO_ROOT = _repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


OUTPUT_COLUMNS = [
    "证券代码",
    "转债简称",
    "收盘价",
    "转股溢价率",
    "溢价率分位",
    "近20日成交额中位数",
    "未来20日最大回撤",
]


def _build_result() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    result = query_to_dataframe(sql_path.read_text(encoding="utf-8"), max_rows=0, timeout=600)
    if result is None:
        raise ValueError("SQL execution returned None")
    return result[OUTPUT_COLUMNS]


def main() -> pd.DataFrame:
    started = time.time()
    result = _build_result()
    if not result.empty:
        result["证券代码"] = result["证券代码"].astype(str)
    result_dir = Path(__file__).resolve().parent
    result.to_csv(result_dir / "result.csv", index=False, encoding="utf-8-sig", float_format="%.2f")
    print(f"# elapsed_seconds={time.time() - started:.2f}", file=sys.stderr)
    return result


if __name__ == "__main__":
    print(main().to_csv(index=False, float_format="%.2f"), end="")
