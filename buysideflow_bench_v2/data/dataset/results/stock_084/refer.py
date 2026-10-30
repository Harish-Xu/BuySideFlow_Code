#!/usr/bin/env python3
from __future__ import annotations

import math
import re
import sys
from pathlib import Path
from string import Template

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


SIGNAL_START_DATE = "2023-01-01"
SIGNAL_END_DATE = "2025-04-30"
PERFORMANCE_END_DATE = "2025-07-31"
BENCHMARK_CODE = "000906"
MAX_HOLDINGS = 20
OUTPUT_PATH = Path(__file__).with_name("result.csv")
SQL_BLOCK_RE = re.compile(
    r"^-- name:\s*(?P<name>[a-zA-Z0-9_]+)\s*$\n(?P<sql>.*?)(?=^-- name:|\Z)",
    re.M | re.S,
)


def query(sql: str, timeout: int = 900) -> pd.DataFrame:
    df = query_to_dataframe(sql, max_rows=0, timeout=timeout)
    if df is None:
        raise RuntimeError("SQL returned None")
    return df


def load_sql_blocks() -> dict[str, str]:
    sql_text = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    blocks = {
        match.group("name"): match.group("sql").strip()
        for match in SQL_BLOCK_RE.finditer(sql_text)
    }
    if not blocks:
        raise ValueError("No SQL blocks found")
    return blocks


SQL_BLOCKS = load_sql_blocks()


def render_sql(name: str, **params: object) -> str:
    if name not in SQL_BLOCKS:
        raise KeyError(f"Missing SQL block: {name}")
    return Template(SQL_BLOCKS[name]).substitute({k: str(v) for k, v in params.items()})


def query_block(name: str, timeout: int = 900, **params: object) -> pd.DataFrame:
    return query(render_sql(name, **params), timeout=timeout)


def sql_date_list(dates: pd.Series | pd.DatetimeIndex | list[pd.Timestamp]) -> str:
    values = sorted({pd.Timestamp(x).strftime("%Y-%m-%d") for x in dates})
    if not values:
        raise ValueError("No dates available for SQL IN clause")
    return ", ".join(f"DATE('{value}')" for value in values)


def sql_int_list(values: pd.Series | list[int], chunk_size: int = 600) -> list[str]:
    unique_values = sorted(pd.Series(values).dropna().astype(int).unique().tolist())
    return [
        ", ".join(str(x) for x in unique_values[i : i + chunk_size])
        for i in range(0, len(unique_values), chunk_size)
    ]


def pct_rank(series: pd.Series, ascending: bool = True) -> pd.Series:
    valid_count = series.notna().sum()
    if valid_count == 0:
        return pd.Series(np.nan, index=series.index)
    if valid_count == 1:
        return pd.Series(1.0, index=series.index)
    return (series.rank(method="min", ascending=ascending) - 1.0) / (valid_count - 1.0)


def fetch_trade_calendar() -> pd.DatetimeIndex:
    df = query_block(
        "trading_days",
        timeout=120,
        calendar_start_date=(pd.Timestamp(SIGNAL_START_DATE) - pd.Timedelta(days=60)).strftime("%Y-%m-%d"),
        calendar_end_date=(pd.Timestamp(PERFORMANCE_END_DATE) + pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
    )
    if df.empty:
        raise ValueError("No trading calendar returned")
    return pd.DatetimeIndex(pd.to_datetime(df["trade_date"]).sort_values())


def fetch_month_end_dates() -> pd.DatetimeIndex:
    df = query_block(
        "month_ends",
        timeout=120,
        signal_start_date=SIGNAL_START_DATE,
        signal_end_date=SIGNAL_END_DATE,
    )
    if df.empty:
        raise ValueError("No month-end observation dates returned")
    return pd.DatetimeIndex(pd.to_datetime(df["obs_date"]).sort_values())


def build_schedule(trade_days: pd.DatetimeIndex, month_ends: pd.DatetimeIndex) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    trade_values = trade_days.to_numpy(dtype="datetime64[ns]")
    obs_dates = [pd.Timestamp(x) for x in month_ends]
    for obs_date in obs_dates:
        buy_pos = int(np.searchsorted(trade_values, np.datetime64(obs_date), side="right"))
        if buy_pos >= len(trade_days):
            raise ValueError(f"No buy date after observation date {obs_date:%Y-%m-%d}")
        buy_date = pd.Timestamp(trade_days[buy_pos])
        july_start = pd.Timestamp(year=obs_date.year, month=7, day=1)
        july_end = pd.Timestamp(year=obs_date.year, month=7, day=31)
        july_trade_days = trade_days[(trade_days >= july_start) & (trade_days <= july_end)]
        if july_trade_days.empty:
            raise ValueError(f"No July trading day for {obs_date.year}")
        sell_date = pd.Timestamp(july_trade_days[-1])
        rows.append(
            {
                "obs_date": obs_date,
                "buy_date": buy_date,
                "sell_date": sell_date,
                "sell_price_type": "close",
            }
        )
    return pd.DataFrame(rows)


def fetch_int_chunks(
    block_name: str,
    values: pd.Series | list[int],
    placeholder: str,
    *,
    chunk_size: int = 500,
    timeout: int = 600,
    **params: object,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for value_clause in sql_int_list(values, chunk_size=chunk_size):
        frames.append(
            query_block(
                block_name,
                timeout=timeout,
                **params,
                **{placeholder: value_clause},
            )
        )
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def fetch_candidates(obs_date: pd.Timestamp) -> pd.DataFrame:
    obs = pd.Timestamp(obs_date)
    candidates = query_block(
        "candidates",
        timeout=900,
        obs_date=obs.strftime("%Y-%m-%d"),
    )
    if candidates.empty:
        return candidates
    candidates["INNERCODE"] = pd.to_numeric(candidates["INNERCODE"], errors="coerce").astype(int)
    candidates["COMPANYCODE"] = pd.to_numeric(candidates["COMPANYCODE"], errors="coerce").astype(int)

    numeric_cols = [
        "market_cap",
        "float_market_cap",
        "cash_dividend_sum_3y",
        "cfo_sum_3y",
        "positive_profit_years_3y",
        "debt_ratio",
        "dividend_yield",
        "ocf_dividend_cover_3y",
    ]
    for col in numeric_cols:
        candidates[col] = pd.to_numeric(candidates[col], errors="coerce")
    return candidates.dropna(subset=numeric_cols)


def select_holdings(schedule: pd.DataFrame, trade_days: pd.DatetimeIndex) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for sched_row in schedule.itertuples(index=False):
        candidates = fetch_candidates(pd.Timestamp(sched_row.obs_date))
        if candidates.empty:
            continue
        candidates = candidates[candidates["cash_dividend_sum_3y"].gt(0)].copy()
        high_count = int(math.ceil(len(candidates) * 0.30))
        if high_count <= 0:
            continue
        high_dividend = candidates.sort_values(
            ["dividend_yield", "stock_code"],
            ascending=[False, True],
        ).head(high_count).copy()
        if high_dividend.empty:
            continue

        high_dividend["quality_score"] = (
            pct_rank(high_dividend["ocf_dividend_cover_3y"], ascending=True)
            + pct_rank(high_dividend["positive_profit_years_3y"], ascending=True)
            + pct_rank(high_dividend["debt_ratio"], ascending=False)
        ) / 3.0
        selected = high_dividend.sort_values(
            ["quality_score", "dividend_yield", "stock_code"],
            ascending=[False, False, True],
        ).head(MAX_HOLDINGS).copy()
        if selected.empty:
            continue
        selected["obs_date"] = pd.Timestamp(sched_row.obs_date)
        selected["buy_date"] = pd.Timestamp(sched_row.buy_date)
        selected["sell_date"] = pd.Timestamp(sched_row.sell_date)
        selected["sell_price_type"] = sched_row.sell_price_type
        selected["weight"] = 1.0 / len(selected)
        selected["position_rank"] = np.arange(1, len(selected) + 1)
        rows.append(selected)
    if not rows:
        raise ValueError("No monthly holdings were selected")
    return pd.concat(rows, ignore_index=True).sort_values(
        ["obs_date", "position_rank", "stock_code"]
    ).reset_index(drop=True)


def fetch_stock_prices(innercodes: pd.Series, dates: pd.Series) -> pd.DataFrame:
    parts = fetch_int_chunks(
        "stock_prices",
        innercodes,
        "inner_values",
        timeout=900,
        price_dates=sql_date_list(dates),
    )
    if parts.empty:
        return parts
    prices = parts.copy()
    prices["INNERCODE"] = pd.to_numeric(prices["INNERCODE"], errors="coerce").astype(int)
    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    for col in ["OPENPRICE", "CLOSEPRICE", "BACKWARDPRICE"]:
        prices[col] = pd.to_numeric(prices[col], errors="coerce")
    prices["adj_open"] = prices["OPENPRICE"] * prices["BACKWARDPRICE"] / prices["CLOSEPRICE"]
    prices["adj_close"] = prices["BACKWARDPRICE"]
    return prices[["INNERCODE", "trade_date", "adj_open", "adj_close"]].drop_duplicates(
        ["INNERCODE", "trade_date"], keep="last"
    )


def fetch_benchmark_prices(dates: pd.Series) -> pd.DataFrame:
    bench = query_block(
        "benchmark_prices",
        timeout=300,
        benchmark_code=BENCHMARK_CODE,
        price_dates=sql_date_list(dates),
    )
    if bench.empty:
        raise ValueError("No benchmark prices returned")
    bench["trade_date"] = pd.to_datetime(bench["trade_date"])
    bench["OPENPRICE"] = pd.to_numeric(bench["OPENPRICE"], errors="coerce")
    bench["CLOSEPRICE"] = pd.to_numeric(bench["CLOSEPRICE"], errors="coerce")
    return bench.drop_duplicates("trade_date", keep="last")


def build_outputs(
    holdings: pd.DataFrame,
    stock_prices: pd.DataFrame,
    benchmark_prices: pd.DataFrame,
    schedule: pd.DataFrame,
) -> pd.DataFrame:
    price_map = stock_prices.set_index(["INNERCODE", "trade_date"]).sort_index()
    bench_map = benchmark_prices.set_index("trade_date").sort_index()
    period_returns: dict[pd.Timestamp, dict[str, float]] = {}

    for obs_date, current in holdings.groupby("obs_date", sort=True):
        current = current.copy()
        buy_date = pd.Timestamp(current["buy_date"].iloc[0])
        sell_date = pd.Timestamp(current["sell_date"].iloc[0])
        sell_price_type = str(current["sell_price_type"].iloc[0])
        stock_returns: list[float] = []
        for row in current.itertuples():
            buy_key = (int(row.INNERCODE), buy_date)
            sell_key = (int(row.INNERCODE), sell_date)
            if buy_key not in price_map.index or sell_key not in price_map.index:
                continue
            buy_price = price_map.at[buy_key, "adj_open"]
            sell_col = "adj_close" if sell_price_type == "close" else "adj_open"
            sell_price = price_map.at[sell_key, sell_col]
            if pd.isna(buy_price) or pd.isna(sell_price) or buy_price <= 0 or sell_price <= 0:
                continue
            stock_returns.append(float(sell_price / buy_price - 1.0))
        if not stock_returns:
            continue
        portfolio_return = float(np.mean(stock_returns))
        if buy_date not in bench_map.index or sell_date not in bench_map.index:
            raise ValueError(f"Benchmark price missing for {buy_date:%Y-%m-%d} to {sell_date:%Y-%m-%d}")
        bench_buy = bench_map.at[buy_date, "OPENPRICE"]
        bench_sell = bench_map.at[sell_date, "CLOSEPRICE" if sell_price_type == "close" else "OPENPRICE"]
        benchmark_return = float(bench_sell / bench_buy - 1.0)
        period_returns[pd.Timestamp(obs_date)] = {
            "holding_count": len(stock_returns),
            "portfolio_return": portfolio_return,
            "benchmark_return": benchmark_return,
            "excess_return": portfolio_return - benchmark_return,
        }

    if not period_returns:
        raise ValueError("No valid holding-period returns could be calculated")

    period_rows = []
    for sched_row in schedule.itertuples(index=False):
        obs_date = pd.Timestamp(sched_row.obs_date)
        values = period_returns.get(obs_date)
        period_rows.append(
            {
                "观察日": obs_date.strftime("%Y-%m-%d"),
                "持有期结束日": pd.Timestamp(sched_row.sell_date).strftime("%Y-%m-%d"),
                "入选股票数": int(values["holding_count"]) if values else 0,
                "组合收益(%)": round(values["portfolio_return"] * 100, 2) if values else np.nan,
                "基准收益(%)": round(values["benchmark_return"] * 100, 2) if values else np.nan,
                "超额收益(%)": round(values["excess_return"] * 100, 2) if values else np.nan,
            }
        )
    period_result = pd.DataFrame(period_rows)
    for col in ["组合收益(%)", "基准收益(%)", "超额收益(%)"]:
        period_result[col] = period_result[col].map(
            lambda value: "" if pd.isna(value) else f"{float(value):.2f}"
        )
    return period_result


def main() -> pd.DataFrame:
    trade_days = fetch_trade_calendar()
    month_ends = fetch_month_end_dates()
    schedule = build_schedule(trade_days, month_ends)
    holdings = select_holdings(schedule, trade_days)
    price_dates = pd.concat([holdings["buy_date"], holdings["sell_date"]], ignore_index=True)
    stock_prices = fetch_stock_prices(holdings["INNERCODE"], price_dates)
    benchmark_prices = fetch_benchmark_prices(price_dates)
    result = build_outputs(holdings, stock_prices, benchmark_prices, schedule)
    result.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False))
    print(f"rows={len(result)}")
    return result


if __name__ == "__main__":
    main()
