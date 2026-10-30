#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


RESULT_COLUMNS = [
    "月份",
    "中期票据",
    "短融及超短融",
]


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    sql = (base_dir / "refer.sql").read_text(encoding="utf-8")
    result = query_to_dataframe(sql, max_rows=0, timeout=600)
    if result is None or result.empty:
        raise ValueError("SQL execution returned no eligible issuance events")

    if list(result.columns) != RESULT_COLUMNS:
        raise ValueError(f"Unexpected result columns: {list(result.columns)}")

    result = result.copy()
    expected_months = pd.period_range("2025-01", "2025-12", freq="M").astype(str).tolist()
    actual_months = result["月份"].astype(str).tolist()
    if actual_months != sorted(set(actual_months)) or not set(actual_months).issubset(expected_months):
        raise ValueError("Expected unique, ascending months within 2025")

    for column in RESULT_COLUMNS[1:]:
        result[column] = pd.to_numeric(result[column], errors="raise").map(lambda value: f"{value:.2f}")

    result.to_csv(base_dir / "result.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
