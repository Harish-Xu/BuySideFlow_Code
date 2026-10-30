#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


RESULT_COLUMNS = ["月份", "月度兑付压力(亿元)", "合计金额(亿元)", "压力最高月份"]


def main() -> pd.DataFrame:
    base_dir = Path(__file__).resolve().parent
    sql = (base_dir / "refer.sql").read_text(encoding="utf-8")
    events = query_to_dataframe(sql, max_rows=0, timeout=300)
    if events is None:
        raise RuntimeError("SQL returned None")

    months = pd.period_range("2025-08", "2025-12", freq="M").astype(str)
    monthly = pd.DataFrame({"月份": months})

    if events.empty:
        monthly["月度兑付压力(亿元)"] = 0.0
        total_amount = 0.0
        max_month = ""
    else:
        events = events.copy()
        events["兑付月份"] = pd.to_datetime(events["pressure_date"]).dt.strftime("%Y-%m")
        events["存续余额_百万元"] = pd.to_numeric(events["size_million"], errors="coerce").fillna(0.0)
        by_month = (
            events.groupby("兑付月份", as_index=False)["存续余额_百万元"]
            .sum()
            .rename(columns={"兑付月份": "月份", "存续余额_百万元": "月度兑付压力_百万元"})
        )
        monthly = monthly.merge(by_month, on="月份", how="left")
        monthly["月度兑付压力_百万元"] = monthly["月度兑付压力_百万元"].fillna(0.0)
        total_amount = monthly["月度兑付压力_百万元"].sum() / 100.0
        max_month = monthly.sort_values(["月度兑付压力_百万元", "月份"], ascending=[False, True]).iloc[0]["月份"]
        monthly["月度兑付压力(亿元)"] = monthly["月度兑付压力_百万元"] / 100.0
        monthly = monthly.drop(columns=["月度兑付压力_百万元"])

    monthly["合计金额(亿元)"] = total_amount
    monthly["压力最高月份"] = max_month
    result = monthly[RESULT_COLUMNS].copy()
    for column in ["月度兑付压力(亿元)", "合计金额(亿元)"]:
        result[column] = result[column].map(lambda value: f"{float(value):.2f}")

    result.to_csv(base_dir / "result.csv", index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
