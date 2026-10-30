#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe, shared_connection_session


SCRIPT_DIR = Path(__file__).resolve().parent
SQL_PATH = SCRIPT_DIR / "refer.sql"
OUTPUT_PATH = SCRIPT_DIR / "result.csv"

OUTPUT_COLUMNS = [
    "股票代码",
    "股票简称短名",
    "当前PE(TTM)",
    "PE过去5年历史分位(%)",
    "近3年归母净利润CAGR(%)",
]

SQL_BLOCK_RE = re.compile(
    r"^-- name:\s*(?P<name>[a-zA-Z0-9_]+)\s*$\n(?P<sql>.*?)(?=^-- name:|\Z)",
    re.M | re.S,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="stock_152 PE percentile and net profit CAGR")
    parser.add_argument("--as-of-date", default="2025-12-26")
    parser.add_argument("--output-path", default=str(OUTPUT_PATH))
    return parser.parse_args()


def load_sql_blocks() -> dict[str, str]:
    sql_text = SQL_PATH.read_text(encoding="utf-8")
    blocks = {
        match.group("name"): match.group("sql").strip()
        for match in SQL_BLOCK_RE.finditer(sql_text)
    }
    if not blocks:
        raise ValueError("No SQL blocks found in refer.sql")
    return blocks


SQL_BLOCKS = load_sql_blocks()


def render_sql(name: str, **params: object) -> str:
    if name not in SQL_BLOCKS:
        raise KeyError(f"Missing SQL block: {name}")
    return SQL_BLOCKS[name].format(**{key: str(value) for key, value in params.items()})


def query_block(name: str, timeout: int = 600, **params: object) -> pd.DataFrame:
    df = query_to_dataframe(render_sql(name, **params), max_rows=0, timeout=timeout)
    if df is None:
        raise RuntimeError(f"SQL returned None: {name}")
    return df


def historical_percentile(history: pd.Series, current_value: float) -> float:
    valid = pd.to_numeric(history, errors="coerce").dropna()
    if valid.empty:
        raise ValueError("Empty PE history")
    return float((valid.le(current_value).sum() / len(valid)) * 100.0)


def build_result(as_of_date: str) -> pd.DataFrame:
    valuation = query_block("valuation_history", as_of_date=as_of_date, timeout=600)
    profit = query_block("annual_profit", as_of_date=as_of_date, timeout=600)
    if valuation.empty:
        raise ValueError("No valuation history returned")
    if profit.empty:
        raise ValueError("No annual profit records returned")

    valuation["pe_ttm"] = pd.to_numeric(valuation["pe_ttm"], errors="coerce")
    valuation["is_current"] = pd.to_numeric(valuation["is_current"], errors="coerce").fillna(0).astype(int)
    valuation = valuation.dropna(subset=["pe_ttm"])

    profit["report_date"] = pd.to_datetime(profit["report_date"])
    profit["net_profit_parent"] = pd.to_numeric(profit["net_profit_parent"], errors="coerce")
    profit = profit.dropna(subset=["net_profit_parent"])

    rows: list[dict[str, object]] = []
    for secucode, group in valuation.groupby("SECUCODE", sort=False):
        current_rows = group[group["is_current"].eq(1)]
        if len(current_rows) != 1:
            raise ValueError(f"Expected one current PE row for {secucode}, got {len(current_rows)}")
        current = current_rows.iloc[0]
        current_pe = float(current["pe_ttm"])
        percentile = historical_percentile(group["pe_ttm"], current_pe)

        profit_rows = profit[profit["SECUCODE"].eq(secucode)]
        profit_map = {
            pd.Timestamp(row.report_date).strftime("%Y-%m-%d"): float(row.net_profit_parent)
            for row in profit_rows.itertuples(index=False)
        }
        if "2021-12-31" not in profit_map or "2024-12-31" not in profit_map:
            raise ValueError(f"Missing 2021/2024 annual profit for {secucode}")
        cagr = (profit_map["2024-12-31"] / profit_map["2021-12-31"]) ** (1.0 / 3.0) - 1.0

        rows.append(
            {
                "股票代码": str(secucode).zfill(6),
                "股票简称短名": current["stock_name"],
                "当前PE(TTM)": current_pe,
                "PE过去5年历史分位(%)": percentile,
                "近3年归母净利润CAGR(%)": cagr * 100.0,
            }
        )

    result = pd.DataFrame(rows)
    if len(result) != 3:
        raise ValueError(f"Expected 3 output rows, got {len(result)}")
    result = result.sort_values(
        ["PE过去5年历史分位(%)", "股票代码"],
        ascending=[False, True],
        kind="mergesort",
    ).reset_index(drop=True)
    return result[OUTPUT_COLUMNS]


def main() -> pd.DataFrame:
    args = parse_args()
    as_of_date = pd.Timestamp(args.as_of_date).strftime("%Y-%m-%d")
    with shared_connection_session():
        result = build_result(as_of_date)

    output = result.copy()
    for col in ["当前PE(TTM)", "PE过去5年历史分位(%)", "近3年归母净利润CAGR(%)"]:
        output[col] = output[col].astype(float).round(2)

    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(output.to_string(index=False))
    return output


if __name__ == "__main__":
    main()
