#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


OUTPUT_COLUMNS = [
    "指数",
    "第一次低点日期",
    "第二次低点日期",
    "低点收盘价偏离幅度(%)",
    "相对两低点均价涨幅(%)",
]


def no_pair_row(group: pd.DataFrame) -> dict[str, object]:
    return {
        "指数": group["index_name"].iloc[-1],
        "第一次低点日期": None,
        "第二次低点日期": None,
        "低点收盘价偏离幅度(%)": None,
        "相对两低点均价涨幅(%)": None,
    }


def find_double_bottom(group: pd.DataFrame) -> dict[str, object]:
    group = group.sort_values("TRADINGDAY").tail(130).reset_index(drop=True)
    if len(group) < 120:
        return no_pair_row(group)

    close = group["CLOSEPRICE"].to_numpy()
    recent_start = len(group) - 120

    local_lows = [
        i
        for i in range(5, len(group) - 5)
        if i >= recent_start and close[i] == close[i - 5 : i + 6].min()
    ]
    valid_pairs = [
        (local_lows[j - 1], local_lows[j])
        for j in range(1, len(local_lows))
        if local_lows[j] - local_lows[j - 1] >= 10
    ]
    if not valid_pairs:
        return no_pair_row(group)

    first_idx, second_idx = valid_pairs[-1]
    first_close = float(close[first_idx])
    second_close = float(close[second_idx])
    latest_close = float(close[-1])
    low_average = (first_close + second_close) / 2.0

    price_deviation = abs(second_close / first_close - 1.0)
    rebound = latest_close / low_average - 1.0

    return {
        "指数": group["index_name"].iloc[-1],
        "第一次低点日期": group.at[first_idx, "TRADINGDAY"].strftime("%Y-%m-%d"),
        "第二次低点日期": group.at[second_idx, "TRADINGDAY"].strftime("%Y-%m-%d"),
        "低点收盘价偏离幅度(%)": round(price_deviation * 100.0, 2),
        "相对两低点均价涨幅(%)": round(rebound * 100.0, 2),
    }


def main() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    quotes = query_to_dataframe(sql, max_rows=0, timeout=600)
    if quotes is None or quotes.empty:
        raise ValueError("No index quote data returned")

    quotes["TRADINGDAY"] = pd.to_datetime(quotes["TRADINGDAY"])
    quotes["CLOSEPRICE"] = pd.to_numeric(quotes["CLOSEPRICE"], errors="coerce")
    quotes = quotes.dropna(subset=["CLOSEPRICE"]).sort_values(["index_name", "TRADINGDAY"])

    result = pd.DataFrame(
        [find_double_bottom(group) for _, group in quotes.groupby("index_name", sort=True)],
        columns=OUTPUT_COLUMNS,
    )
    result.to_csv(
        Path(__file__).with_name("result.csv"),
        index=False,
        encoding="utf-8-sig",
        float_format="%.2f",
    )
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
