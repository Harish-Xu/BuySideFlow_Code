#!/usr/bin/env python3
from __future__ import annotations

import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from sweagent.text2sql.db_connector import query_to_dataframe

BASE_DIR = Path(__file__).resolve().parent
RESULT_COLUMNS = ["转债代码", "转债简称", "回售期起始日", "回售触发价", "距触发价幅度"]


def build_result(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        raise ValueError("No observable H2-2025 put-period candidates")
    records = []
    for _, group in raw.groupby("INNERCODE", sort=False):
        # Separate payment-year rows describe the same first contractual put period.
        fields = ["MAINCODE", "SECUCODE", "SECUABBR", "put_start_date",
                  "trigger_ratio_pct", "conversion_price", "stock_price"]
        if any(group[column].nunique(dropna=False) > 1 for column in fields):
            raise ValueError("Conflicting contractual terms or observation-date prices")
        row = group.iloc[0]
        if any(pd.isna(row[field]) for field in
               ("trigger_ratio_pct", "conversion_price", "stock_price")):
            continue
        ratio, conversion, stock = (Decimal(str(row[field])) for field in
                                    ("trigger_ratio_pct", "conversion_price", "stock_price"))
        if any(not value.is_finite() or value <= 0 for value in (ratio, conversion, stock)):
            continue
        trigger = conversion * ratio / Decimal(100)
        distance = stock / trigger - 1
        records.append({"code": str(row["SECUCODE"]), "name": row["SECUABBR"],
                        "maincode": row["MAINCODE"],
                        "start": pd.Timestamp(row["put_start_date"]).strftime("%Y-%m-%d"),
                        "trigger": trigger, "distance": distance})
    if not records:
        raise ValueError("No complete put-period candidates")
    if len({row["maincode"] for row in records}) != len(records):
        raise ValueError("Multiple market codes for one convertible bond require review")
    records.sort(key=lambda row: (row["distance"], row["code"]))
    result = [{"转债代码": row["code"], "转债简称": row["name"],
               "回售期起始日": row["start"],
               "回售触发价": row["trigger"].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
               "距触发价幅度": (row["distance"] * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)}
              for row in records[:30]]
    return pd.DataFrame(result, columns=RESULT_COLUMNS)


def main() -> pd.DataFrame:
    sql = globals().get("sql_code") or (BASE_DIR / "refer.sql").read_text(encoding="utf-8")
    raw = query_to_dataframe(sql, max_rows=0, timeout=180)
    result = build_result(raw)
    if "sql_code" not in globals():
        result.to_csv(BASE_DIR / "result.csv", index=False, encoding="utf-8-sig")
    return result


if __name__ == "__main__":
    result = main()
    print(result)
