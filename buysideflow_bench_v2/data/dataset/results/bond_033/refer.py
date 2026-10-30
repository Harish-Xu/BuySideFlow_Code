#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe, shared_connection_session


AS_OF_DATE = "2025-11-26"
TRADE_WINDOW = 60
MIN_TRADE_DAYS = 5
BATCH_SIZE = 1000
RESULT_COLUMNS = [
    "挂牌类型",
    "券只数",
    "成交天数中位数",
    "日均成交金额中位数",
]


def load_candidates(base_dir: Path) -> pd.DataFrame:
    sql = (base_dir / "refer.sql").read_text(encoding="utf-8")
    candidates = query_to_dataframe(sql, max_rows=0, timeout=600)
    if candidates is None or candidates.empty:
        raise ValueError("No eligible bonds returned")
    required = ["MAINCODE", "INNERCODE", "SECUMARKET"]
    missing = [column for column in required if column not in candidates.columns]
    if missing:
        raise ValueError(f"Candidate SQL missing columns: {missing}")
    candidates = candidates[required].drop_duplicates().copy()
    for column in required:
        candidates[column] = pd.to_numeric(candidates[column], errors="raise").astype("int64")
    return candidates


def load_trade_days() -> list[str]:
    sql = f"""
    SELECT DATE_FORMAT(trade_day, '%Y-%m-%d') AS trade_day
    FROM (
        SELECT DISTINCT DATE(TRADINGDAY) AS trade_day
        FROM bond_basicquote
        WHERE DATE(TRADINGDAY) <= DATE('{AS_OF_DATE}')
        ORDER BY trade_day DESC
        LIMIT {TRADE_WINDOW}
    ) d
    ORDER BY trade_day ASC
    """
    days = query_to_dataframe(sql, max_rows=0, timeout=300)
    if days is None or len(days) != TRADE_WINDOW:
        got = 0 if days is None else len(days)
        raise ValueError(f"Expected {TRADE_WINDOW} trading days, got {got}")
    return days["trade_day"].astype(str).tolist()


def load_quotes(candidates: pd.DataFrame, trade_days: list[str]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    listing_codes = candidates["INNERCODE"].drop_duplicates().tolist()
    first_day = trade_days[0]
    last_day = trade_days[-1]

    with shared_connection_session():
        for start in range(0, len(listing_codes), BATCH_SIZE):
            batch = listing_codes[start : start + BATCH_SIZE]
            # Quote venues and listing markets use different SECUMARKET enums.
            sql = f"""
            SELECT
                q.INNERCODE,
                q.SECUMARKET,
                DATE_FORMAT(q.TRADINGDAY, '%Y-%m-%d') AS trade_day,
                MAX(
                    CASE
                        WHEN q.IFTRADING = 1
                          OR COALESCE(q.TURNOVERVALUE, 0) > 0
                          OR COALESCE(q.TURNOVERVOLUME, 0) > 0
                        THEN 1 ELSE 0
                    END
                ) AS traded_flag,
                SUM(CASE WHEN COALESCE(q.TURNOVERVALUE, 0) > 0 THEN q.TURNOVERVALUE ELSE 0 END) AS turnover_yuan
            FROM bond_basicquote q FORCE INDEX (IX_BOND_BASICQUOTE)
            WHERE q.INNERCODE IN ({', '.join(str(int(code)) for code in batch)})
              AND q.TRADINGDAY >= DATE('{first_day}')
              AND q.TRADINGDAY < DATE_ADD(DATE('{last_day}'), INTERVAL 1 DAY)
              AND (
                  q.IFTRADING = 1
                  OR COALESCE(q.TURNOVERVALUE, 0) > 0
                  OR COALESCE(q.TURNOVERVOLUME, 0) > 0
              )
            GROUP BY q.INNERCODE, q.SECUMARKET, q.TRADINGDAY
            """
            frame = query_to_dataframe(sql, max_rows=0, timeout=600)
            if frame is not None and not frame.empty:
                frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=["MAINCODE", "trade_day", "traded_flag", "turnover_yuan"])
    quotes = pd.concat(frames, ignore_index=True)
    quotes["INNERCODE"] = pd.to_numeric(quotes["INNERCODE"], errors="raise").astype("int64")
    quotes["SECUMARKET"] = pd.to_numeric(quotes["SECUMARKET"], errors="raise").astype("int64")
    quotes["traded_flag"] = pd.to_numeric(quotes["traded_flag"], errors="coerce").fillna(0).astype("int64")
    quotes["turnover_yuan"] = pd.to_numeric(quotes["turnover_yuan"], errors="coerce").fillna(0.0)
    quotes = quotes.merge(
        candidates[["MAINCODE", "INNERCODE"]].drop_duplicates(),
        on="INNERCODE",
        how="inner",
        validate="many_to_one",
    )
    return quotes[["MAINCODE", "trade_day", "traded_flag", "turnover_yuan"]]


def build_result(candidates: pd.DataFrame, quotes: pd.DataFrame, trade_days: list[str]) -> pd.DataFrame:
    market_counts = candidates.groupby("MAINCODE")["SECUMARKET"].nunique()
    listing_type = market_counts.map(lambda count: "跨市场挂牌券" if count > 1 else "单市场挂牌券")
    bond_base = listing_type.rename("挂牌类型").reset_index()

    if quotes.empty:
        main_day = pd.DataFrame(columns=["MAINCODE", "trade_day", "traded_flag", "turnover_yuan"])
    else:
        main_day = (
            quotes.groupby(["MAINCODE", "trade_day"], as_index=False)
            .agg(traded_flag=("traded_flag", "max"), turnover_yuan=("turnover_yuan", "sum"))
        )

    liquidity = (
        main_day.groupby("MAINCODE", as_index=False)
        .agg(trade_days_60=("traded_flag", "sum"), turnover_yuan_60=("turnover_yuan", "sum"))
        if not main_day.empty
        else pd.DataFrame(columns=["MAINCODE", "trade_days_60", "turnover_yuan_60"])
    )
    liquidity = bond_base.merge(liquidity, on="MAINCODE", how="left").fillna({"trade_days_60": 0, "turnover_yuan_60": 0.0})
    liquidity = liquidity[liquidity["trade_days_60"] >= MIN_TRADE_DAYS].copy()
    liquidity["日均成交金额"] = liquidity["turnover_yuan_60"] / len(trade_days) / 100000000.0

    result = (
        liquidity.groupby("挂牌类型")
        .agg(
            券只数=("MAINCODE", "nunique"),
            成交天数中位数=("trade_days_60", "median"),
            日均成交金额中位数=("日均成交金额", "median"),
        )
        .reset_index()
    )
    order = {"跨市场挂牌券": 1, "单市场挂牌券": 2}
    result["_order"] = result["挂牌类型"].map(order)
    result = result.sort_values("_order").drop(columns="_order")
    result["券只数"] = result["券只数"].astype(int)
    result["成交天数中位数"] = result["成交天数中位数"].astype(float).map(lambda value: f"{value:.1f}")
    result["日均成交金额中位数"] = result["日均成交金额中位数"].astype(float).map(lambda value: f"{value:.4f}")
    return result[RESULT_COLUMNS]


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    candidates = load_candidates(base_dir)
    trade_days = load_trade_days()
    quotes = load_quotes(candidates, trade_days)
    result = build_result(candidates, quotes, trade_days)
    result.to_csv(base_dir / "result.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
