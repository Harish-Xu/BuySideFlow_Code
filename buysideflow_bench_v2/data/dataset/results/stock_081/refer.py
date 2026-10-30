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


OBSERVATION_DATE = "2025-04-30"
BUILD_DATE = "2025-05-06"
REPORT_END_DATE = "2025-03-31"
FINANCIAL_START_DATE = "2022-01-01"
CALENDAR_START_DATE = "2025-03-01"
CALENDAR_END_DATE = "2025-08-31"
BENCHMARK_CODE = "000852"
TOP_N = 30
MIN_AVG_TURNOVER_20 = 50_000_000.0
HORIZONS = (20, 60)
SQL_BLOCK_RE = re.compile(
    r"^-- name:\s*(?P<name>[a-zA-Z0-9_]+)\s*$\n(?P<sql>.*?)(?=^-- name:|\Z)",
    re.M | re.S,
)
FACTOR_COLUMNS = {
    "OPERATINGREVENUEYOY": "revenue_yoy_improvement",
    "NPPARENTCOMPANYCUTYOY": "cut_np_yoy_improvement",
    "NETOPERATECASHFLOWYOY": "cfo_yoy_improvement",
}


def query(sql: str, timeout: int = 600) -> pd.DataFrame:
    df = query_to_dataframe(sql, max_rows=0, timeout=timeout)
    if df is None:
        raise RuntimeError("SQL returned None")
    return df


def load_sql_blocks() -> dict[str, str]:
    sql_path = Path(__file__).with_name("refer.sql")
    sql_text = sql_path.read_text(encoding="utf-8")
    blocks = {
        match.group("name"): match.group("sql").strip()
        for match in SQL_BLOCK_RE.finditer(sql_text)
    }
    if not blocks:
        raise ValueError(f"No named SQL blocks found in {sql_path}")
    return blocks


SQL_BLOCKS = load_sql_blocks()


def render_sql(name: str, **params: object) -> str:
    if name not in SQL_BLOCKS:
        raise KeyError(f"Missing SQL block: {name}")
    normalized = {key: str(value) for key, value in params.items()}
    return Template(SQL_BLOCKS[name]).substitute(**normalized)


def query_block(name: str, timeout: int = 600, **params: object) -> pd.DataFrame:
    return query(render_sql(name, **params), timeout=timeout)


def format_int_list(values: pd.Series | list[int] | np.ndarray) -> str:
    unique_values = sorted(pd.unique(pd.Series(values).dropna()).astype(int))
    if not unique_values:
        raise ValueError("No integer values available for SQL IN clause")
    return ", ".join(str(x) for x in unique_values)


def fetch_trading_days() -> pd.DataFrame:
    return query_block(
        "trading_days",
        timeout=120,
        calendar_start_date=CALENDAR_START_DATE,
        calendar_end_date=CALENDAR_END_DATE,
    )


def fetch_stock_master(as_of_date: pd.Timestamp) -> pd.DataFrame:
    return query_block(
        "stock_master",
        timeout=240,
        as_of_date=as_of_date.strftime("%Y-%m-%d"),
    )


def fetch_financial_sync(companycodes: pd.Series, as_of_date: pd.Timestamp) -> pd.DataFrame:
    codes = sorted(pd.unique(companycodes.dropna()).astype(int))
    parts: list[pd.DataFrame] = []
    for i in range(0, len(codes), 500):
        parts.append(
            query_block(
                "financial_sync",
                timeout=300,
                company_values=format_int_list(codes[i : i + 500]),
                financial_start_date=FINANCIAL_START_DATE,
                report_end_date=REPORT_END_DATE,
                as_of_date=as_of_date.strftime("%Y-%m-%d"),
            )
        )
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def fetch_stock_quotes(
    innercodes: pd.Series,
    quote_start_date: pd.Timestamp,
    quote_end_date: pd.Timestamp,
) -> pd.DataFrame:
    codes = sorted(pd.unique(innercodes.dropna()).astype(int))
    parts: list[pd.DataFrame] = []
    for i in range(0, len(codes), 300):
        parts.append(
            query_block(
                "stock_quotes",
                timeout=600,
                innercode_values=format_int_list(codes[i : i + 300]),
                quote_start_date=quote_start_date.strftime("%Y-%m-%d"),
                quote_end_date=quote_end_date.strftime("%Y-%m-%d"),
            )
        )
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def fetch_benchmark_quotes(
    quote_start_date: pd.Timestamp,
    quote_end_date: pd.Timestamp,
) -> pd.DataFrame:
    return query_block(
        "benchmark_quotes",
        timeout=180,
        benchmark_code=BENCHMARK_CODE,
        quote_start_date=quote_start_date.strftime("%Y-%m-%d"),
        quote_end_date=quote_end_date.strftime("%Y-%m-%d"),
    )


def get_calendar_points(
    trading_days: pd.DataFrame,
) -> tuple[pd.Timestamp, pd.Timestamp, list[pd.Timestamp], dict[int, pd.Timestamp]]:
    dates = pd.Series(pd.to_datetime(trading_days["trade_date"]).sort_values().unique())
    target = pd.Timestamp(OBSERVATION_DATE)
    eligible_dates = dates[dates <= target]
    if eligible_dates.empty:
        raise ValueError("No trading day is available on or before observation date")
    observation_date = pd.Timestamp(eligible_dates.iloc[-1])
    obs_pos = dates.searchsorted(observation_date)
    if obs_pos < 19:
        raise ValueError("Trading calendar does not contain 20 turnover days before observation")

    build_date = pd.Timestamp(BUILD_DATE)
    build_matches = dates[dates == build_date]
    if build_matches.empty:
        raise ValueError(f"Build date {BUILD_DATE} is not an A-share trading day")
    build_pos = dates.searchsorted(build_date)
    horizon_dates: dict[int, pd.Timestamp] = {}
    for horizon in HORIZONS:
        future_pos = build_pos + horizon
        if future_pos >= len(dates):
            raise ValueError(f"Trading calendar does not cover T+{horizon}")
        horizon_dates[horizon] = pd.Timestamp(dates.iloc[future_pos])
    turnover_dates = [pd.Timestamp(x) for x in dates.iloc[obs_pos - 19 : obs_pos + 1]]
    return observation_date, build_date, turnover_dates, horizon_dates


def preprocess_financial_sync(financial_sync: pd.DataFrame) -> pd.DataFrame:
    fin = financial_sync.copy()
    if fin.empty:
        return fin
    fin["COMPANYCODE"] = pd.to_numeric(fin["COMPANYCODE"], errors="coerce").astype("Int64")
    fin["end_date"] = pd.to_datetime(fin["end_date"])
    fin["pub_date"] = pd.to_datetime(fin["pub_date"])
    for col in FACTOR_COLUMNS:
        fin[col] = pd.to_numeric(fin[col], errors="coerce")
    fin["mark_priority"] = pd.to_numeric(fin["MARK"], errors="coerce").map(
        {1: 1, 8: 2, 7: 3, 6: 4, 2: 5}
    ).fillna(99)
    fin = fin.sort_values(
        ["COMPANYCODE", "end_date", "mark_priority", "pub_date", "INSERTTIME", "ID", "JSID"],
        ascending=[True, True, True, False, False, False, False],
        na_position="last",
    )
    return fin.drop_duplicates(["COMPANYCODE", "end_date"], keep="first").reset_index(drop=True)


def build_factor_panel(financial_sync: pd.DataFrame) -> pd.DataFrame:
    fin = preprocess_financial_sync(financial_sync)
    if fin.empty:
        return pd.DataFrame()

    report_end = pd.Timestamp(REPORT_END_DATE)
    expected_history_dates = {
        report_end - pd.offsets.QuarterEnd(quarter)
        for quarter in range(1, 9)
    }
    rows: list[dict[str, object]] = []
    for companycode, grp in fin.groupby("COMPANYCODE", sort=False):
        grp = grp.sort_values("end_date").reset_index(drop=True)
        current = grp[grp["end_date"] == report_end]
        if current.empty:
            continue
        current_row = current.iloc[-1]
        history = grp[grp["end_date"].isin(expected_history_dates)].copy()
        if set(history["end_date"]) != expected_history_dates:
            continue
        history = history.sort_values("end_date")

        row: dict[str, object] = {
            "COMPANYCODE": int(companycode),
            "latest_end_date": report_end,
            "latest_pub_date": current_row["pub_date"],
        }
        valid = True
        for source_col, target_col in FACTOR_COLUMNS.items():
            current_value = current_row[source_col]
            hist_values = history[source_col]
            if pd.isna(current_value) or hist_values.isna().any():
                valid = False
                break
            row[target_col] = float(current_value - hist_values.median())
        if valid:
            rows.append(row)
    return pd.DataFrame(rows)


def build_investable_pool(
    stock_master: pd.DataFrame,
    stock_quotes: pd.DataFrame,
    turnover_dates: list[pd.Timestamp],
) -> pd.DataFrame:
    master = stock_master.copy()
    master["INNERCODE"] = pd.to_numeric(master["INNERCODE"], errors="coerce").astype(int)
    master["COMPANYCODE"] = pd.to_numeric(master["COMPANYCODE"], errors="coerce").astype(int)
    master["SECUCODE"] = master["SECUCODE"].astype(str).str.zfill(6)
    master["stock_name"] = master["stock_name"].astype(str)

    quotes = stock_quotes.copy()
    quotes["INNERCODE"] = pd.to_numeric(quotes["INNERCODE"], errors="coerce").astype(int)
    quotes["trade_date"] = pd.to_datetime(quotes["trade_date"])
    quotes["TURNOVERVALUE"] = pd.to_numeric(quotes["TURNOVERVALUE"], errors="coerce")
    quotes["BACKWARDPRICE"] = pd.to_numeric(quotes["BACKWARDPRICE"], errors="coerce")

    turnover_set = set(pd.to_datetime(turnover_dates))
    turnover = (
        quotes[quotes["trade_date"].isin(turnover_set)]
        .groupby("INNERCODE", as_index=False)
        .agg(
            turnover_day_count=("TURNOVERVALUE", "count"),
            avg_turnover_20=("TURNOVERVALUE", "mean"),
        )
    )
    pool = master.merge(turnover, on="INNERCODE", how="left")
    return pool[
        pool["turnover_day_count"].eq(20)
        & pool["avg_turnover_20"].ge(MIN_AVG_TURNOVER_20)
    ].copy()


def build_selected_holdings(
    investable_pool: pd.DataFrame,
    factor_panel: pd.DataFrame,
) -> pd.DataFrame:
    factor_cols = list(FACTOR_COLUMNS.values())
    candidates = investable_pool.merge(factor_panel, on="COMPANYCODE", how="inner")
    candidates = candidates.dropna(subset=factor_cols).copy()
    if candidates.empty:
        raise ValueError("No stocks passed all factor requirements")

    for col in factor_cols:
        candidates[f"{col}_pct"] = candidates[col].rank(
            method="max",
            pct=True,
            ascending=True,
        )
    candidates["sync_score"] = candidates[[f"{col}_pct" for col in factor_cols]].mean(axis=1)
    selected = candidates.sort_values(
        ["sync_score", "SECUCODE"],
        ascending=[False, True],
    ).head(TOP_N).copy()
    if selected.empty:
        raise ValueError("No selected holdings")
    selected["position_rank"] = np.arange(1, len(selected) + 1)
    return selected.reset_index(drop=True)


def apply_build_execution(
    selected: pd.DataFrame,
    stock_quotes: pd.DataFrame,
    build_date: pd.Timestamp,
) -> pd.DataFrame:
    quotes = stock_quotes.copy()
    quotes["INNERCODE"] = pd.to_numeric(quotes["INNERCODE"], errors="coerce").astype(int)
    quotes["trade_date"] = pd.to_datetime(quotes["trade_date"])
    quotes["BACKWARDPRICE"] = pd.to_numeric(quotes["BACKWARDPRICE"], errors="coerce")
    executable_codes = set(
        quotes.loc[
            quotes["trade_date"].eq(build_date) & quotes["BACKWARDPRICE"].gt(0),
            "INNERCODE",
        ].astype(int)
    )
    holdings = selected[selected["INNERCODE"].isin(executable_codes)].copy()
    if holdings.empty:
        raise ValueError("None of the selected stocks has a valid build-date close")
    holdings["weight"] = 1.0 / len(holdings)
    return holdings.reset_index(drop=True)


def price_at(quotes: pd.DataFrame, innercode: int, trade_date: pd.Timestamp) -> float:
    matched = quotes[
        (quotes["INNERCODE"] == int(innercode))
        & (quotes["trade_date"] == pd.Timestamp(trade_date))
    ]["BACKWARDPRICE"]
    if matched.empty:
        return math.nan
    return float(matched.iloc[-1])


def latest_price_on_or_before(
    quotes: pd.DataFrame,
    innercode: int,
    trade_date: pd.Timestamp,
) -> float:
    matched = quotes[
        (quotes["INNERCODE"] == int(innercode))
        & (quotes["trade_date"] <= pd.Timestamp(trade_date))
        & quotes["BACKWARDPRICE"].gt(0)
    ].sort_values("trade_date")
    if matched.empty:
        return math.nan
    return float(matched.iloc[-1]["BACKWARDPRICE"])


def build_result(
    selected: pd.DataFrame,
    stock_quotes: pd.DataFrame,
    benchmark_quotes: pd.DataFrame,
    build_date: pd.Timestamp,
    horizon_dates: dict[int, pd.Timestamp],
) -> pd.DataFrame:
    quotes = stock_quotes.copy()
    quotes["INNERCODE"] = pd.to_numeric(quotes["INNERCODE"], errors="coerce").astype(int)
    quotes["trade_date"] = pd.to_datetime(quotes["trade_date"])
    quotes["BACKWARDPRICE"] = pd.to_numeric(quotes["BACKWARDPRICE"], errors="coerce")

    benchmark = benchmark_quotes.copy()
    benchmark["trade_date"] = pd.to_datetime(benchmark["trade_date"])
    benchmark["close_price"] = pd.to_numeric(benchmark["close_price"], errors="coerce")
    benchmark = benchmark.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    benchmark_prices = benchmark.set_index("trade_date")["close_price"]

    if build_date not in benchmark_prices.index:
        raise ValueError("Benchmark base price missing")
    benchmark_base = float(benchmark_prices.loc[build_date])

    rows: list[dict[str, object]] = []
    for horizon, future_date in horizon_dates.items():
        returns: list[float] = []
        for holding in selected.itertuples(index=False):
            base_px = price_at(quotes, int(holding.INNERCODE), build_date)
            future_px = latest_price_on_or_before(
                quotes,
                int(holding.INNERCODE),
                future_date,
            )
            if pd.notna(base_px) and pd.notna(future_px) and base_px > 0 and future_px > 0:
                returns.append(future_px / base_px - 1.0)
        if not returns:
            raise ValueError(f"No valid holding returns for T+{horizon}")
        if future_date not in benchmark_prices.index:
            raise ValueError(f"Benchmark price missing for T+{horizon}")
        benchmark_return = float(benchmark_prices.loc[future_date] / benchmark_base - 1.0)
        portfolio_return = float(np.mean(returns))
        rows.append(
            {
                "收益截止日": future_date.strftime("%Y-%m-%d"),
                "持有期": f"{horizon}日",
                "组合收益": round(portfolio_return, 6),
                "基准收益": round(benchmark_return, 6),
                "超额收益": round(portfolio_return - benchmark_return, 6),
            }
        )
    return pd.DataFrame(rows)


def main() -> pd.DataFrame:
    trading_days = fetch_trading_days()
    observation_date, build_date, turnover_dates, horizon_dates = get_calendar_points(
        trading_days
    )

    stock_master = fetch_stock_master(observation_date)
    financial_sync = fetch_financial_sync(stock_master["COMPANYCODE"], observation_date)
    factor_panel = build_factor_panel(financial_sync)
    factor_master = stock_master[
        stock_master["COMPANYCODE"].isin(factor_panel["COMPANYCODE"])
    ].copy()
    signal_quotes = fetch_stock_quotes(
        factor_master["INNERCODE"],
        min(turnover_dates),
        build_date,
    )

    investable_pool = build_investable_pool(
        factor_master,
        signal_quotes,
        turnover_dates,
    )
    selected = build_selected_holdings(investable_pool, factor_panel)
    selected = apply_build_execution(selected, signal_quotes, build_date)
    return_quotes = fetch_stock_quotes(
        selected["INNERCODE"],
        build_date,
        max(horizon_dates.values()),
    )
    benchmark_quotes = fetch_benchmark_quotes(
        build_date,
        max(horizon_dates.values()),
    )
    result = build_result(
        selected,
        return_quotes,
        benchmark_quotes,
        build_date,
        horizon_dates,
    )

    output_path = Path(__file__).with_name("result.csv")
    result.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.6f")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
