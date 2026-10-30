#!/usr/bin/env python3
"""2024 年报第 11—20 大持仓披露后三个月的静态收益贡献。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = "2025-08-15"
OUTPUT_COLUMNS = ["披露日", "有效股票数量", "有效持仓总权重", "静态收益贡献"]


def query(sql: str) -> pd.DataFrame:
    frame = query_to_dataframe(sql, max_rows=0, timeout=600)
    if frame is None:
        raise RuntimeError("SQL 未返回结果")
    return frame


def load_holdings() -> pd.DataFrame:
    return query(f"""
        WITH fund AS (
            SELECT INNERCODE
            FROM secumain
            WHERE SECUCODE = '000001' AND SECUCATEGORY = 8
            LIMIT 1
        ), first_disclosure AS (
            SELECT MIN(DATE(h.INFOPUBLDATE)) AS disclosure_date
            FROM mf_stockportfoliodetail h
            JOIN fund f ON f.INNERCODE = h.INNERCODE
            WHERE h.REPORTDATE = '2024-12-31'
              AND h.INFOPUBLDATE < DATE_ADD('{AS_OF_DATE}', INTERVAL 1 DAY)
        ), ranked AS (
            SELECT DATE(h.INFOPUBLDATE) AS disclosure_date,
                   h.SERIALNUMBER AS holding_rank,
                   h.STOCKINNERCODE AS stock_id,
                   s.LISTEDSECTOR AS listed_sector,
                   h.RATIOINNV AS weight,
                   ROW_NUMBER() OVER (
                       PARTITION BY h.SERIALNUMBER
                       ORDER BY h.INFOPUBLDATE DESC, h.INSERTTIME DESC, h.ID DESC
                   ) AS rn
            FROM mf_stockportfoliodetail h
            JOIN fund f ON f.INNERCODE = h.INNERCODE
            JOIN secumain s ON s.INNERCODE = h.STOCKINNERCODE
            JOIN first_disclosure fd ON DATE(h.INFOPUBLDATE) = fd.disclosure_date
            WHERE h.REPORTDATE = '2024-12-31'
              AND h.SERIALNUMBER BETWEEN 11 AND 20
        )
        SELECT disclosure_date, holding_rank, stock_id, listed_sector, weight
        FROM ranked WHERE rn = 1
        ORDER BY holding_rank
    """)


def trading_endpoints(disclosure_date: pd.Timestamp) -> tuple[str, str] | None:
    start = disclosure_date.date().isoformat()
    end = (disclosure_date + pd.DateOffset(months=3)).date().isoformat()
    if end > AS_OF_DATE:
        return None
    calendar = query(f"""
        SELECT DISTINCT DATE(TRADINGDATE) AS trading_date
        FROM qt_tradingdaynew
        WHERE IFTRADINGDAY = 1 AND SECUMARKET IN (83, 90)
          AND TRADINGDATE > '{start}'
          AND TRADINGDATE <= LEAST('{end}', '{AS_OF_DATE}')
        ORDER BY trading_date
    """)
    if calendar.empty:
        return None
    return str(calendar.iloc[0, 0]), str(calendar.iloc[-1, 0])


def load_prices(stock_ids: list[int], start: str, end: str,
                table: str, price_column: str) -> pd.DataFrame:
    if not stock_ids:
        return pd.DataFrame()
    ids = ",".join(str(int(code)) for code in stock_ids)
    return query(f"""
        WITH ranked AS (
            SELECT INNERCODE AS stock_id, DATE(TRADINGDAY) AS trading_date,
                   {price_column} AS price,
                   ROW_NUMBER() OVER (
                       PARTITION BY INNERCODE, DATE(TRADINGDAY)
                       ORDER BY UPDATETIME DESC, JSID DESC, ID DESC
                   ) AS rn
            FROM {table}
            WHERE INNERCODE IN ({ids})
              AND TRADINGDAY IN ('{start}', '{end}')
        )
        SELECT stock_id, trading_date, price FROM ranked WHERE rn = 1
    """)


def load_stib_actions(stock_ids: list[int], start: str, end: str) -> pd.DataFrame:
    if not stock_ids:
        return pd.DataFrame()
    ids = ",".join(str(int(code)) for code in stock_ids)
    return query(f"""
        WITH ranked AS (
            SELECT INNERCODE AS stock_id, DATE(EXDIVIDATE) AS ex_date,
                   COALESCE(BONUSSHRATIOADJUSTED, BONUSSHARERATIO, 0) AS bonus,
                   COALESCE(TRANADDRATIOADJUSTED, TRANADDRATIO, 0) AS transfer,
                   COALESCE(CASHDIVIRMBADJUSTED, CASHDIVIRMB, 0) AS cash,
                   ROW_NUMBER() OVER (
                       PARTITION BY INNERCODE, DATE(EXDIVIDATE), SCHEMENO
                       ORDER BY INFOPUBLDATE DESC, INSERTTIME DESC, ID DESC
                   ) AS rn
            FROM lc_stibdividend
            WHERE INNERCODE IN ({ids})
              AND EXDIVIDATE > '{start}' AND EXDIVIDATE <= '{end}'
              AND INFOPUBLDATE < DATE_ADD('{AS_OF_DATE}', INTERVAL 1 DAY)
              AND EVENTPROCEDURE = 3131 AND IFDIVIDEND = 1
        )
        SELECT stock_id, ex_date, bonus, transfer, cash
        FROM ranked WHERE rn = 1
        ORDER BY stock_id, ex_date
    """)


def calculate(holdings: pd.DataFrame, prices: pd.DataFrame,
              stib_prices: pd.DataFrame, stib_actions: pd.DataFrame,
              disclosure_date: pd.Timestamp, start: str, end: str) -> pd.DataFrame:
    def pivot(frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame()
        frame = frame.copy()
        frame["trading_date"] = frame["trading_date"].astype(str)
        return frame.pivot(index="stock_id", columns="trading_date", values="price")

    price_table = pivot(prices)
    stib_table = pivot(stib_prices)
    total_weight = 0.0
    contribution = 0.0
    count = 0
    for row in holdings.itertuples(index=False):
        stock_id = int(row.stock_id)
        is_stib = row.listed_sector == 7
        table = stib_table if is_stib else price_table
        if stock_id not in table.index or pd.isna(row.weight):
            continue
        start_price = pd.to_numeric(table.at[stock_id, start], errors="coerce") if start in table.columns else float("nan")
        end_price = pd.to_numeric(table.at[stock_id, end], errors="coerce") if end in table.columns else float("nan")
        if pd.isna(start_price) or pd.isna(end_price) or start_price <= 0 or end_price <= 0:
            continue
        if is_stib and not stib_actions.empty:
            # 科创板无预计算后复权价：按实施的每10股送转及派现还原一股期初持仓的期末价值。
            shares = 1.0
            cash_received = 0.0
            for action in stib_actions[stib_actions["stock_id"] == stock_id].itertuples(index=False):
                cash_received += shares * float(action.cash) / 10.0
                shares *= 1.0 + (float(action.bonus) + float(action.transfer)) / 10.0
            end_price = shares * end_price + cash_received
        weight = float(row.weight)
        total_weight += weight
        contribution += weight * (end_price / start_price - 1.0)
        count += 1
    return pd.DataFrame([{
        "披露日": disclosure_date.strftime("%Y-%m-%d"),
        "有效股票数量": count,
        "有效持仓总权重": f"{total_weight * 100:.2f}",
        "静态收益贡献": f"{contribution * 100:.4f}",
    }], columns=OUTPUT_COLUMNS)


def main() -> pd.DataFrame:
    holdings = load_holdings()
    result = pd.DataFrame(columns=OUTPUT_COLUMNS)
    if not holdings.empty:
        disclosure_date = pd.Timestamp(holdings["disclosure_date"].iloc[0])
        endpoints = trading_endpoints(disclosure_date)
        if endpoints is not None:
            holdings = holdings.dropna(subset=["stock_id"]).copy()
            if not holdings.empty:
                regular_ids = holdings.loc[holdings["listed_sector"] != 7, "stock_id"].astype(int).tolist()
                stib_ids = holdings.loc[holdings["listed_sector"] == 7, "stock_id"].astype(int).tolist()
                prices = load_prices(regular_ids, *endpoints, "qt_performancedata", "BACKWARDPRICE")
                stib_prices = load_prices(stib_ids, *endpoints, "lc_stibdailyquote", "CLOSEPRICE")
                stib_actions = load_stib_actions(stib_ids, *endpoints)
                result = calculate(holdings, prices, stib_prices, stib_actions, disclosure_date, *endpoints)
    result.to_csv(Path(__file__).with_name("result.csv"), index=False, encoding="utf-8-sig")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    main()
