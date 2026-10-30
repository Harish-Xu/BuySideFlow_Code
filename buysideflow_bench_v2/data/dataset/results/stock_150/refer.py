#!/usr/bin/env python3
from __future__ import annotations

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
    "股票简称",
    "持仓市值(亿元)",
    "占流通市值比例(%)",
    "是否二季度新进",
]
SQL_BLOCK_RE = re.compile(
    r"^-- name:\s*(?P<name>[a-zA-Z0-9_]+)\s*$\n(?P<sql>.*?)(?=^-- name:|\Z)",
    re.M | re.S,
)


def load_sql_blocks() -> dict[str, str]:
    text = SQL_PATH.read_text(encoding="utf-8")
    blocks = {
        match.group("name"): match.group("sql").strip()
        for match in SQL_BLOCK_RE.finditer(text)
    }
    if not blocks:
        raise ValueError("No SQL blocks found in refer.sql")
    return blocks


SQL_BLOCKS = load_sql_blocks()


def query_block(name: str, **params: object) -> pd.DataFrame:
    sql = SQL_BLOCKS[name].format(**{key: str(value) for key, value in params.items()})
    frame = query_to_dataframe(sql, max_rows=0, timeout=300)
    if frame is None:
        raise RuntimeError(f"SQL returned None: {name}")
    return frame


def build_result() -> pd.DataFrame:
    trade = query_block("trade_date")
    if trade.empty or pd.isna(trade.loc[0, "trade_date"]):
        raise ValueError("No trading day found on or before the observation date")
    trade_date = pd.Timestamp(trade.loc[0, "trade_date"]).strftime("%Y-%m-%d")

    holders = query_block("shareholder_panel")
    stocks = query_block("stock_map")
    prices = query_block("price_snapshot", trade_date=trade_date)
    valuations = query_block("valuation_snapshot", trade_date=trade_date)
    if holders.empty or stocks.empty or prices.empty or valuations.empty:
        raise ValueError("Required shareholder or market data is empty")

    holders["report_date"] = pd.to_datetime(holders["report_date"])
    for column in ["COMPANYCODE", "HOLDSUM", "JSID", "ID"]:
        holders[column] = pd.to_numeric(holders[column], errors="coerce")
    holders = holders.sort_values(
        ["COMPANYCODE", "report_date", "shareholder_name", "INFOPUBLDATE", "JSID", "ID"],
        ascending=[True, True, True, False, False, False],
        kind="mergesort",
    ).drop_duplicates(["COMPANYCODE", "report_date", "shareholder_name"], keep="first")

    q1_companies = set(
        holders.loc[
            holders["report_date"].eq(pd.Timestamp("2025-03-31"))
            & holders["HOLDSUM"].gt(0),
            "COMPANYCODE",
        ].dropna()
    )
    h1 = holders[
        holders["report_date"].eq(pd.Timestamp("2025-06-30"))
        & holders["HOLDSUM"].gt(0)
    ]
    h1 = h1.groupby("COMPANYCODE", as_index=False)["HOLDSUM"].sum()

    stocks["COMPANYCODE"] = pd.to_numeric(stocks["COMPANYCODE"], errors="coerce")
    stocks["INNERCODE"] = pd.to_numeric(stocks["INNERCODE"], errors="coerce")
    stocks["stock_code"] = stocks["stock_code"].astype(str).str.zfill(6)
    stocks = stocks.sort_values(["COMPANYCODE", "stock_code", "INNERCODE"]).drop_duplicates(
        "COMPANYCODE", keep="first"
    )

    for frame, value_column in [(prices, "CLOSEPRICE"), (valuations, "NEGOTIABLEMV")]:
        for column in ["INNERCODE", value_column, "JSID", "ID"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame.sort_values(["INNERCODE", "JSID", "ID"], ascending=[True, False, False], inplace=True)
        frame.drop_duplicates("INNERCODE", keep="first", inplace=True)

    panel = (
        h1.merge(stocks, on="COMPANYCODE", how="inner")
        .merge(prices[["INNERCODE", "CLOSEPRICE"]], on="INNERCODE", how="inner")
        .merge(valuations[["INNERCODE", "NEGOTIABLEMV"]], on="INNERCODE", how="inner")
    )
    panel["holding_value"] = panel["HOLDSUM"] * panel["CLOSEPRICE"]
    panel["float_share"] = panel["holding_value"] / panel["NEGOTIABLEMV"] * 100
    panel["is_new"] = panel["COMPANYCODE"].map(
        lambda company: "否" if company in q1_companies else "是"
    )
    panel = panel.sort_values(["holding_value", "stock_code"], ascending=[False, True]).head(10)

    result = pd.DataFrame(
        {
            "股票代码": panel["stock_code"],
            "股票简称": panel["stock_name"],
            "持仓市值(亿元)": panel["holding_value"] / 100000000,
            "占流通市值比例(%)": panel["float_share"],
            "是否二季度新进": panel["is_new"],
        }
    )
    result[["持仓市值(亿元)", "占流通市值比例(%)"]] = result[
        ["持仓市值(亿元)", "占流通市值比例(%)"]
    ].round(2)
    return result[OUTPUT_COLUMNS].reset_index(drop=True)


def main() -> pd.DataFrame:
    with shared_connection_session():
        result = build_result()
    result.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
