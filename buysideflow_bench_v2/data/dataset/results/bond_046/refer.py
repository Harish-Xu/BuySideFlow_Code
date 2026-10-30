#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


RESULT_COLUMNS = ["发行人", "存续债数量", "平均估值收益率", "下调事件次数"]


def format_result(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    result = raw.copy()
    result["平均估值收益率"] = pd.to_numeric(result["平均估值收益率"], errors="coerce").map(
        lambda value: "" if pd.isna(value) else f"{float(value):.2f}"
    )
    result["存续债数量"] = pd.to_numeric(result["存续债数量"], errors="raise").astype("Int64")
    result["下调事件次数"] = pd.to_numeric(result["下调事件次数"], errors="raise").astype("Int64")
    result["发行人"] = result["发行人"].astype(str)
    return result[RESULT_COLUMNS]


def main() -> pd.DataFrame:
    base_dir = Path(__file__).resolve().parent
    sql = (base_dir / "refer.sql").read_text(encoding="utf-8")
    raw = query_to_dataframe(sql, max_rows=0, timeout=600)
    if raw is None:
        raise RuntimeError("SQL returned None")

    result = format_result(raw)
    result.to_csv(base_dir / "result.csv", index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
