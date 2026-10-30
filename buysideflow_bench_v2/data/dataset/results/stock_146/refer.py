#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


SCRIPT_DIR = Path(__file__).resolve().parent
EXPECTED_COLUMNS = [
    "portfolio_return",
    "csi800_total_return",
    "excess_return",
]


def main() -> pd.DataFrame:
    sql = (SCRIPT_DIR / "refer.sql").read_text(encoding="utf-8")
    result = query_to_dataframe(sql, max_rows=0, timeout=120)

    if result.shape != (1, len(EXPECTED_COLUMNS)):
        raise ValueError(f"预期返回1行3列，实际为 {result.shape}")
    if list(result.columns) != EXPECTED_COLUMNS:
        raise ValueError(f"输出列不符合题面要求：{list(result.columns)}")

    result = result.copy()
    result[EXPECTED_COLUMNS] = result[EXPECTED_COLUMNS].apply(
        pd.to_numeric, errors="raise"
    ).round(6)
    result.to_csv(
        SCRIPT_DIR / "result.csv",
        index=False,
        encoding="utf-8-sig",
        float_format="%.6f",
    )
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
