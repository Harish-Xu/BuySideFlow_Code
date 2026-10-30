#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe  # noqa: E402


AS_OF_DATE = "2024-06-30"
LOOKBACK_DAYS = 20
TOP_N = 20
CHUNK_SIZE = 3000
QUERY_TIMEOUT = 180

BANK_QUOTE_MARKETS = (89,)
# Exchange listings use bond_code.SECUMARKET 18/83/90. bond_basicquote has
# exchange-side quote rows with SECUMARKET=12 for some SSE-listed bonds, even
# though generic market dictionaries elsewhere do not enumerate it.
EXCHANGE_QUOTE_MARKETS = (12, 83)

RESULT_COLUMNS = [
    "银行间债券代码",
    "上交所债券代码",
    "债券简称",
    "价格日期",
    "银行间净价",
    "上交所净价",
    "银行间近20日成交天数",
    "上交所近20日成交天数",
    "净价差",
]

def sql_list(values) -> str:
    return ",".join(str(int(v)) for v in values)


def chunks(values, size=CHUNK_SIZE):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start : start + size]


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def fetch_candidates() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=QUERY_TIMEOUT)
    if df.empty:
        return df
    for col in ["MAINCODE", "INNERCODE", "SECUMARKET"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    return df.dropna(subset=["MAINCODE", "INNERCODE", "SECUMARKET", "SECUCODE", "SECUABBR", "market_group"])


def fetch_trade_dates() -> list[str]:
    sql = f"""
    SELECT DATE(TRADINGDATE) AS trade_day
    FROM qt_tradingdaynew
    WHERE TRADINGDATE <= DATE('{AS_OF_DATE}')
      AND IFTRADINGDAY = 1
      AND SECUMARKET IN (83, 89)
    GROUP BY DATE(TRADINGDATE)
    HAVING COUNT(DISTINCT SECUMARKET) = 2
    ORDER BY DATE(TRADINGDATE) DESC
    LIMIT {LOOKBACK_DAYS}
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=60)
    if df.empty:
        return []
    return sorted(pd.to_datetime(df["trade_day"]).dt.strftime("%Y-%m-%d").tolist())


def fetch_quotes_for_side(candidates: pd.DataFrame, trade_dates: list[str], side: str, quote_markets: tuple[int, ...]) -> pd.DataFrame:
    side_codes = candidates[candidates["market_group"] == side].copy()
    if side_codes.empty or not trade_dates:
        return pd.DataFrame()

    date_sql = ",".join(f"'{d}'" for d in trade_dates)
    market_sql = ",".join(str(v) for v in quote_markets)
    frames: list[pd.DataFrame] = []
    for part in chunks(side_codes["INNERCODE"].dropna().astype(int).unique()):
        sql = f"""
        SELECT
            INNERCODE,
            SECUMARKET AS quote_market,
            DATE(TRADINGDAY) AS price_date,
            IFTRADING,
            CLOSENETPRICE,
            TURNOVERVALUE
        FROM bond_basicquote FORCE INDEX (IX_BOND_BASICQUOTE)
        WHERE INNERCODE IN ({sql_list(part)})
          AND SECUMARKET IN ({market_sql})
          AND TRADINGDAY IN ({date_sql})
          AND TURNOVERVALUE IS NOT NULL
          AND TURNOVERVALUE > 0
          AND INSERTTIME < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY)
        """
        frame = query_to_dataframe(sql, max_rows=0, timeout=QUERY_TIMEOUT)
        if not frame.empty:
            frames.append(frame)

    if not frames:
        return pd.DataFrame()

    quotes = pd.concat(frames, ignore_index=True)
    quotes["INNERCODE"] = pd.to_numeric(quotes["INNERCODE"], errors="coerce").astype("Int64")
    quotes["quote_market"] = pd.to_numeric(quotes["quote_market"], errors="coerce").astype("Int64")
    quotes["price_date"] = pd.to_datetime(quotes["price_date"], errors="coerce")
    quotes["CLOSENETPRICE"] = pd.to_numeric(quotes["CLOSENETPRICE"], errors="coerce")
    quotes["TURNOVERVALUE"] = pd.to_numeric(quotes["TURNOVERVALUE"], errors="coerce").fillna(0.0)
    quotes = quotes.dropna(subset=["INNERCODE", "quote_market", "price_date"])

    merged = quotes.merge(
        side_codes[["MAINCODE", "INNERCODE", "SECUCODE", "SECUABBR", "SECUMARKET", "market_group"]],
        on="INNERCODE",
        how="inner",
    )
    return merged


def listing_summary(quotes: pd.DataFrame) -> pd.DataFrame:
    if quotes.empty:
        return pd.DataFrame()
    return (
        quotes.groupby(["MAINCODE", "market_group"], dropna=False)
        .agg(
            trade_days_20=("price_date", "nunique"),
            turnover_20d=("TURNOVERVALUE", "sum"),
        )
        .reset_index()
    )


def choose_quote(rows: pd.DataFrame, target_date: pd.Timestamp | None = None) -> pd.Series:
    work = rows.copy()
    if target_date is not None:
        work = work[work["price_date"] == target_date].copy()
    work = work.sort_values(
        ["price_date", "TURNOVERVALUE", "CLOSENETPRICE", "SECUCODE"],
        ascending=[False, False, False, True],
    )
    return work.iloc[0]


def attach_summary(rows: pd.DataFrame, summary: pd.DataFrame) -> pd.DataFrame:
    keys = ["MAINCODE", "market_group"]
    merged = rows.merge(summary, on=keys, how="left")
    if merged[["trade_days_20", "turnover_20d"]].isna().any().any():
        raise ValueError("missing listing summary")
    return merged


def build_pair(maincode: int, bank_rows: pd.DataFrame, exchange_rows: pd.DataFrame, summary: pd.DataFrame) -> dict[str, object] | None:
    bank_eligible = attach_summary(bank_rows, summary)
    exchange_eligible = attach_summary(exchange_rows, summary)
    bank_eligible = bank_eligible[bank_eligible["trade_days_20"] >= 3]
    exchange_eligible = exchange_eligible[exchange_eligible["trade_days_20"] >= 3]
    if bank_eligible.empty or exchange_eligible.empty:
        return None

    common_dates = set(bank_eligible["price_date"]) & set(exchange_eligible["price_date"])
    if not common_dates:
        return None

    selected_date = max(common_dates)
    bank_quote = choose_quote(bank_eligible, selected_date)
    exchange_quote = choose_quote(exchange_eligible, selected_date)
    bank_price = float(bank_quote["CLOSENETPRICE"])
    exchange_price = float(exchange_quote["CLOSENETPRICE"])
    if pd.isna(bank_price) or pd.isna(exchange_price) or min(bank_price, exchange_price) <= 0:
        return None
    price_diff = bank_price - exchange_price

    return {
        "MAINCODE": int(maincode),
        "银行间债券代码": bank_quote["SECUCODE"],
        "上交所债券代码": exchange_quote["SECUCODE"],
        "债券简称": bank_quote["SECUABBR"],
        "银行间净价": bank_price,
        "上交所净价": exchange_price,
        "价格日期": pd.to_datetime(bank_quote["price_date"]).strftime("%Y-%m-%d"),
        "银行间近20日成交天数": int(bank_quote["trade_days_20"]),
        "上交所近20日成交天数": int(exchange_quote["trade_days_20"]),
        "净价差": price_diff,
    }


def build_result() -> pd.DataFrame:
    candidates = fetch_candidates()
    if candidates.empty:
        return empty_result()
    trade_dates = fetch_trade_dates()
    bank_quotes = fetch_quotes_for_side(candidates, trade_dates, "bank", BANK_QUOTE_MARKETS)
    exchange_quotes = fetch_quotes_for_side(candidates, trade_dates, "exchange", EXCHANGE_QUOTE_MARKETS)
    if bank_quotes.empty or exchange_quotes.empty:
        return empty_result()

    quotes = pd.concat([bank_quotes, exchange_quotes], ignore_index=True)
    summary = listing_summary(quotes)
    bank_groups = {int(k): v for k, v in bank_quotes.groupby("MAINCODE", sort=False)}
    exchange_groups = {int(k): v for k, v in exchange_quotes.groupby("MAINCODE", sort=False)}
    rows = []
    for maincode in sorted(set(bank_groups) & set(exchange_groups)):
        row = build_pair(maincode, bank_groups[maincode], exchange_groups[maincode], summary)
        if row is not None:
            rows.append(row)
    if not rows:
        return empty_result()

    result = pd.DataFrame(rows)
    result["净价差_abs"] = result["净价差"].abs()
    result = result.sort_values(["净价差_abs", "MAINCODE"], ascending=[False, True]).head(TOP_N).reset_index(drop=True)

    for col in ["银行间净价", "上交所净价", "净价差"]:
        result[col] = result[col].map(lambda value: f"{value:.4f}")

    if result[["银行间债券代码", "上交所债券代码"]].duplicated().any():
        raise ValueError("duplicated cross-market pair")
    if (result["银行间近20日成交天数"] < 3).any() or (result["上交所近20日成交天数"] < 3).any():
        raise ValueError("result contains a side with fewer than 3 traded days in the 20-day window")
    if not result.empty and not (result["净价差_abs"].diff().iloc[1:] <= 1e-12).all():
        raise ValueError("result is not sorted by absolute net-price spread descending")

    return result[RESULT_COLUMNS]


def write_result(result: pd.DataFrame) -> pd.DataFrame:
    out_path = Path(__file__).with_name("result.csv")
    result.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    write_result(build_result())
