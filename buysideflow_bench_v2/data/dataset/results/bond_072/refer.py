#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "sweagent").exists() and (parent / "data").exists():
            return parent
    raise RuntimeError("Cannot locate repository root")


REPO_ROOT = _repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


RESULT_COLUMNS = ["事件数量", "规模减少额合计(亿元)"]


def main() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    result = query_to_dataframe(sql, max_rows=0, timeout=600)
    if result is None:
        raise ValueError("SQL execution returned None")
    result = result[RESULT_COLUMNS].copy()
    if len(result) != 1 or result.isna().any().any():
        raise ValueError("expected one non-null aggregate row")
    result["事件数量"] = result["事件数量"].astype("int64")
    return result


if __name__ == "__main__":
    out = main()
    out.to_csv(Path(__file__).with_name("result.csv"), index=False, encoding="utf-8-sig", float_format="%.4f")
    print(out.to_string(index=False))
