#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


ETF_CODES = ["510300", "513100", "518880"]
PAIR_COLUMNS = [
    ("510300", "513100", "协方差_510300_513100"),
    ("510300", "518880", "协方差_510300_518880"),
    ("513100", "518880", "协方差_513100_518880"),
]
PRICE_WINDOW = 500
RETURN_OBS = PRICE_WINDOW - 1


def build_result(quotes: pd.DataFrame) -> pd.DataFrame:
    required = {"ETF代码", "ETF名称", "交易日", "收盘价"}
    missing = required.difference(quotes.columns)
    if missing:
        raise ValueError(f"SQL result missing columns: {sorted(missing)}")

    df = quotes.copy()
    df["ETF代码"] = df["ETF代码"].astype(str)
    df["交易日"] = pd.to_datetime(df["交易日"])
    df["收盘价"] = pd.to_numeric(df["收盘价"], errors="coerce")
    df = df.dropna(subset=["ETF代码", "交易日", "收盘价"])

    unexpected = sorted(set(df["ETF代码"]) - set(ETF_CODES))
    if unexpected:
        raise ValueError(f"Unexpected ETF codes returned: {unexpected}")

    name_map = (
        df.sort_values(["ETF代码", "交易日"])
        .drop_duplicates("ETF代码", keep="last")
        .set_index("ETF代码")["ETF名称"]
        .to_dict()
    )
    if set(name_map) != set(ETF_CODES):
        raise ValueError(f"Expected ETF codes {ETF_CODES}, got {sorted(name_map)}")

    prices = (
        df.pivot_table(
            index="交易日",
            columns="ETF代码",
            values="收盘价",
            aggfunc="last",
        )
        .sort_index()
        .loc[:, ETF_CODES]
    )
    prices = prices.dropna(how="any")
    if len(prices) != PRICE_WINDOW:
        raise ValueError(f"Expected {PRICE_WINDOW} common close-price days, got {len(prices)}")

    returns = prices.pct_change().dropna(how="any")
    if len(returns) != RETURN_OBS:
        raise ValueError(f"Expected {RETURN_OBS} return observations, got {len(returns)}")

    mean_returns = returns.mean()
    variances = returns.var(ddof=0)
    cov_matrix = returns.cov(ddof=0)

    rows: list[dict[str, object]] = []
    for code in sorted(ETF_CODES):
        row: dict[str, object] = {
            "ETF代码": code,
            "ETF名称": name_map[code],
            "日均收益率": mean_returns.loc[code],
            "方差": variances.loc[code],
        }
        for left, right, column_name in PAIR_COLUMNS:
            row[column_name] = cov_matrix.loc[left, right]
        rows.append(row)

    return pd.DataFrame(rows)


def main() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    quotes = query_to_dataframe(sql, max_rows=0, timeout=600)
    if quotes is None:
        raise ValueError("SQL execution returned None")

    result = build_result(quotes)
    result.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.6f")
    print(result.to_string(index=False, float_format=lambda value: f"{value:.6f}"))
    return result


if __name__ == "__main__":
    main()
