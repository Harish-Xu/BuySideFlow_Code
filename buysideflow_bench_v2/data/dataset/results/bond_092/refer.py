#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = "2025-06-30"

RESULT_COLUMNS = [
    "债券代码",
    "债券简称",
    "发行人",
    "剩余期限(年)",
    "中债推荐到期收益率(%)",
]

def fetch_candidate_pool() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    return query_to_dataframe(sql, max_rows=0, timeout=300)


def format_result(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    result = df[["bond_code", "bond_name", "issuer", "remaining_years", "valuation_yield_pct"]].copy()
    result["bond_code"] = result["bond_code"].astype(str)
    for col in ["remaining_years", "valuation_yield_pct"]:
        result[col] = pd.to_numeric(result[col])
    result = result.sort_values(["valuation_yield_pct", "bond_code"], ascending=[False, True])
    result[["remaining_years", "valuation_yield_pct"]] = result[["remaining_years", "valuation_yield_pct"]].round(4)
    result.columns = RESULT_COLUMNS
    return result.reset_index(drop=True)


def main() -> None:
    result = format_result(fetch_candidate_pool())
    out_path = Path(__file__).with_name("result.csv")
    result.to_csv(out_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    print(f"as_of={AS_OF_DATE}, rows={len(result)}, output={out_path}")


if __name__ == "__main__":
    main()
