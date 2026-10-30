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
    "行业",
    "债券只数",
    "余额合计(亿元)",
    "估值收益率中位数(%)",
    "余额加权平均剩余期限(年)",
]


def format_result(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    result["债券只数"] = pd.to_numeric(result["债券只数"], errors="raise").astype(int)
    for column in ["余额合计(亿元)", "估值收益率中位数(%)", "余额加权平均剩余期限(年)"]:
        result[column] = pd.to_numeric(result[column], errors="raise").map(lambda value: f"{value:.2f}")
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
