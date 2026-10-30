#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path
from string import Template

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


SQL_BLOCK_RE = re.compile(
    r"^-- name:\s*(?P<name>[a-zA-Z0-9_]+)\s*$\n(?P<sql>.*?)(?=^-- name:|\Z)",
    re.MULTILINE | re.DOTALL,
)


def load_sql_blocks() -> dict[str, str]:
    text = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    blocks = {m.group("name"): m.group("sql").strip() for m in SQL_BLOCK_RE.finditer(text)}
    if set(blocks) != {"schedule", "monthly_result"}:
        raise ValueError("refer.sql must contain schedule and monthly_result blocks")
    return blocks


def run_query(sql: str, timeout: int) -> pd.DataFrame:
    df = query_to_dataframe(sql, max_rows=0, timeout=timeout)
    if df is None:
        raise ValueError("SQL execution returned None")
    return df


def main() -> pd.DataFrame:
    blocks = load_sql_blocks()
    schedule = run_query(blocks["schedule"], timeout=120)
    rows: list[pd.DataFrame] = []
    for item in schedule.itertuples(index=False):
        obs_date = pd.Timestamp(item.observation_date).strftime("%Y-%m-%d")
        future_date = pd.Timestamp(item.future_date).strftime("%Y-%m-%d")
        sql = Template(blocks["monthly_result"]).substitute(
            obs_date=obs_date,
            future_date=future_date,
        )
        monthly = run_query(sql, timeout=180)
        if not monthly.empty:
            rows.append(monthly)

    if not rows:
        result = pd.DataFrame(
            columns=[
                "观察日",
                "最低PE组平均超额收益",
                "最高PE组平均超额收益",
                "低PE组减高PE组收益差",
            ]
        )
    else:
        result = pd.concat(rows, ignore_index=True).sort_values("观察日").reset_index(drop=True)

    output_path = Path(__file__).with_name("result.csv")
    result.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    main()
