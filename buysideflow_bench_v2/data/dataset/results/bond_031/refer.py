#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


DETAIL_COLUMNS = [
    "债券代码",
    "债券简称",
    "发行人",
    "首次违约日",
    "是否有下调",
    "评级机构",
    "下调公告日",
    "调低前评级",
    "调低后评级",
    "距违约天数",
]

def load_detail(base_dir: Path) -> pd.DataFrame:
    sql = (base_dir / "refer.sql").read_text(encoding="utf-8")
    detail = query_to_dataframe(sql, max_rows=0, timeout=600)
    if detail is None:
        raise ValueError("SQL execution returned None")

    missing = [column for column in DETAIL_COLUMNS if column not in detail.columns]
    if missing:
        raise ValueError(f"SQL result missing columns: {missing}")

    detail = detail[DETAIL_COLUMNS].copy()
    detail = detail.fillna("")
    if not detail.empty:
        distance = pd.to_numeric(detail["距违约天数"], errors="coerce")
        detail["距违约天数"] = distance.map(lambda value: "" if pd.isna(value) else str(int(value)))
    return detail


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    detail = load_detail(base_dir)

    detail.to_csv(base_dir / "result.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
