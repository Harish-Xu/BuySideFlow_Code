#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from string import Template

import numpy as np
import pandas as pd

os.environ.setdefault("DB_CONNECT_TIMEOUT_SEC", "10")

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


PRODUCTION_CODE = 110166522
NEW_ORDER_CODE = 110166521
AS_OF_DATE = "2025-11-05"
START_MONTH = "2025-01"
CALENDAR_START_DATE = "2025-01-01"
CALENDAR_END_DATE = "2025-12-31"
HISTORY_WINDOW = 60
PRODUCTION_PERCENTILE_THRESHOLD = 0.45
ORDER_MINUS_PRODUCTION_THRESHOLD = 0.10
HOLDING_DAYS = 20
BENCHMARK_CODE = "000300"
INDUSTRY_CODES = (
    "801010",
    "801030",
    "801040",
    "801050",
    "801080",
    "801110",
    "801120",
    "801130",
    "801140",
    "801150",
    "801160",
    "801170",
    "801180",
    "801200",
    "801210",
    "801230",
    "801710",
    "801720",
    "801730",
    "801740",
    "801750",
    "801760",
    "801770",
    "801780",
    "801790",
    "801880",
    "801890",
    "801950",
    "801960",
    "801970",
    "801980",
)
SQL_BLOCK_RE = re.compile(
    r"^-- name:\s*(?P<name>[a-zA-Z0-9_]+)\s*$\n(?P<sql>.*?)(?=^-- name:|\Z)",
    re.M | re.S,
)


def query(sql: str, timeout: int = 600, retries: int = 2) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            df = query_to_dataframe(sql, max_rows=0, timeout=timeout)
            if df is None:
                raise RuntimeError("SQL returned None")
            return df
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            print(
                f"query_retry={attempt + 1}/{retries} "
                f"error={type(exc).__name__}: {exc}",
                flush=True,
            )
    raise last_error if last_error is not None else RuntimeError("SQL query failed")


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


def render_sql(name: str, **params: str | int) -> str:
    if name not in SQL_BLOCKS:
        raise KeyError(f"Missing SQL block: {name}")
    return Template(SQL_BLOCKS[name]).substitute(**params)


def query_block(name: str, timeout: int = 600, **params: str | int) -> pd.DataFrame:
    return query(render_sql(name, **params), timeout=timeout)


def format_string_literals(values: list[str] | tuple[str, ...]) -> str:
    unique_values = sorted(set(values))
    if not unique_values:
        raise ValueError("No string values available for SQL IN clause")
    return ", ".join("'" + value.replace("'", "''") + "'" for value in unique_values)


def format_int_list(values: pd.Series | list[int] | np.ndarray) -> str:
    unique_values = sorted(pd.unique(pd.Series(values).dropna()).astype(int))
    if not unique_values:
        raise ValueError("No integer values available for SQL IN clause")
    return ", ".join(str(value) for value in unique_values)


def format_date_literals(values: pd.Series | list[pd.Timestamp] | np.ndarray) -> str:
    dates = pd.to_datetime([value for value in pd.Series(values).dropna().tolist()])
    unique_dates = sorted({date.strftime("%Y-%m-%d") for date in dates})
    if not unique_dates:
        raise ValueError("No dates available for SQL IN clause")
    return ", ".join(f"DATE('{date}')" for date in unique_dates)


def fetch_pmi_values() -> pd.DataFrame:
    first_month = pd.Period(START_MONTH, freq="M")
    as_of = pd.Timestamp(AS_OF_DATE)
    df = query_block(
        "pmi_values",
        timeout=180,
        production_code=PRODUCTION_CODE,
        new_order_code=NEW_ORDER_CODE,
        history_start_month=(first_month - HISTORY_WINDOW + 1).start_time.strftime("%Y-%m-%d"),
        month_after_asof=(as_of.to_period("M") + 1).start_time.strftime("%Y-%m-%d"),
        next_day_ts=(as_of + pd.Timedelta(days=1)).strftime("%Y-%m-%d 00:00:00"),
    )
    if df.empty:
        raise ValueError("PMI query returned no rows")
    df["indicator_code"] = pd.to_numeric(df["indicator_code"], errors="raise").astype(int)
    df["month_end"] = pd.to_datetime(df["month_end"])
    df["month"] = df["month_end"].dt.to_period("M")
    df["publish_time"] = pd.to_datetime(df["publish_time"])
    df["update_time"] = pd.to_datetime(df["update_time"])
    df["indicator_value"] = pd.to_numeric(df["indicator_value"], errors="raise")
    return df


def build_pmi_signals(pmi: pd.DataFrame) -> pd.DataFrame:
    ordered = pmi.sort_values(
        ["indicator_code", "month", "publish_time", "update_time", "jsid", "record_id"]
    )
    first_publish = (
        ordered[ordered["indicator_value"].notna()]
        .groupby(["month", "indicator_code"])["publish_time"]
        .min()
        .unstack()
    )
    candidate_months = pd.period_range(START_MONTH, pd.Period(AS_OF_DATE, freq="M"), freq="M")

    rows: list[dict[str, object]] = []
    for month in candidate_months:
        if month not in first_publish.index:
            continue
        release_times = first_publish.loc[month].reindex([PRODUCTION_CODE, NEW_ORDER_CODE])
        if release_times.isna().any():
            continue
        publish_date = release_times.max().normalize()
        window_months = pd.period_range(month - HISTORY_WINDOW + 1, month, freq="M")
        available = ordered[
            ordered["month"].isin(window_months)
            & (ordered["publish_time"] < publish_date + pd.Timedelta(days=1))
        ].drop_duplicates(["indicator_code", "month"], keep="last")
        values = available.set_index(["indicator_code", "month"])["indicator_value"]
        production_window = values.reindex(
            pd.MultiIndex.from_product([[PRODUCTION_CODE], window_months])
        )
        order_window = values.reindex(
            pd.MultiIndex.from_product([[NEW_ORDER_CODE], window_months])
        )
        if production_window.isna().any() or order_window.isna().any():
            continue
        production_value = float(production_window.iloc[-1])
        order_value = float(order_window.iloc[-1])
        production_count = int((production_window <= production_value).sum())
        order_count = int((order_window <= order_value).sum())
        rows.append(
            {
                "month_end": month.to_timestamp(how="end").normalize(),
                "publish_date": publish_date,
                "production_index": production_value,
                "new_order_index": order_value,
                "production_percentile": production_count / HISTORY_WINDOW,
                "new_order_percentile": order_count / HISTORY_WINDOW,
                "is_event": (
                    production_count / HISTORY_WINDOW < PRODUCTION_PERCENTILE_THRESHOLD
                    and (order_count - production_count) / HISTORY_WINDOW
                    >= ORDER_MINUS_PRODUCTION_THRESHOLD
                ),
            }
        )

    signals = pd.DataFrame(rows)
    if signals.empty:
        raise ValueError("No released 2025 PMI month has a complete 60-month history")
    return signals


def fetch_trading_days() -> pd.Series:
    df = query_block(
        "trading_days",
        timeout=120,
        calendar_start_date=CALENDAR_START_DATE,
        calendar_end_date=CALENDAR_END_DATE,
    )
    if df.empty:
        raise ValueError("Trading calendar query returned no rows")
    return pd.to_datetime(df["trade_date"]).sort_values().drop_duplicates().reset_index(drop=True)


def attach_event_dates(events: pd.DataFrame, trading_days: pd.Series) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for row in events.to_dict("records"):
        publish_date = pd.Timestamp(row["publish_date"])
        event_pos = int(trading_days.searchsorted(publish_date, side="right"))
        horizon_pos = event_pos + HOLDING_DAYS
        if event_pos >= len(trading_days) or horizon_pos >= len(trading_days):
            raise ValueError(f"Insufficient trading calendar after publish date {publish_date:%Y-%m-%d}")
        row["event_date"] = trading_days.iloc[event_pos]
        row["horizon_date"] = trading_days.iloc[horizon_pos]
        rows.append(row)
    return pd.DataFrame(rows)


def fetch_indices() -> pd.DataFrame:
    index_codes = (BENCHMARK_CODE, *INDUSTRY_CODES)
    df = query_block(
        "indices",
        timeout=120,
        benchmark_code=BENCHMARK_CODE,
        index_code_values=format_string_literals(index_codes),
    )
    if df.empty:
        raise ValueError("Index lookup returned no rows")
    df["inner_code"] = pd.to_numeric(df["inner_code"], errors="raise").astype(int)
    expected_codes = set(index_codes)
    found_codes = set(df["index_code"].astype(str))
    missing_codes = sorted(expected_codes - found_codes)
    if missing_codes:
        raise ValueError(f"Missing index codes in secumain: {missing_codes}")
    duplicated_codes = sorted(df.loc[df["index_code"].duplicated(), "index_code"].astype(str).unique())
    if duplicated_codes:
        raise ValueError(f"Duplicated active index codes in secumain: {duplicated_codes}")
    return df


def fetch_index_quotes(indices: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    required_dates = pd.concat([events["event_date"], events["horizon_date"]], ignore_index=True)
    df = query_block(
        "index_quotes",
        timeout=180,
        inner_code_values=format_int_list(indices["inner_code"]),
        date_values=format_date_literals(required_dates),
    )
    if df.empty:
        raise ValueError("Index quote query returned no rows")
    df["inner_code"] = pd.to_numeric(df["inner_code"], errors="raise").astype(int)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["close_price"] = pd.to_numeric(df["close_price"], errors="raise")
    expected_rows = len(indices) * required_dates.nunique()
    if len(df.drop_duplicates(["inner_code", "trade_date"])) < expected_rows:
        raise ValueError("Index quotes are incomplete for at least one event/horizon date")
    return df


def calculate_event_returns(
    events: pd.DataFrame,
    indices: pd.DataFrame,
    quotes: pd.DataFrame,
) -> pd.DataFrame:
    benchmark_inner = int(indices.loc[indices["index_code"] == BENCHMARK_CODE, "inner_code"].iloc[0])
    industries = indices[indices["index_code"].isin(INDUSTRY_CODES)].copy()
    quote_map = quotes.set_index(["inner_code", "trade_date"])["close_price"]

    rows: list[dict[str, object]] = []
    for event in events.to_dict("records"):
        event_date = pd.Timestamp(event["event_date"])
        horizon_date = pd.Timestamp(event["horizon_date"])
        benchmark_start = float(quote_map.loc[(benchmark_inner, event_date)])
        benchmark_end = float(quote_map.loc[(benchmark_inner, horizon_date)])
        benchmark_return = benchmark_end / benchmark_start - 1
        for industry in industries.to_dict("records"):
            industry_inner = int(industry["inner_code"])
            industry_start = float(quote_map.loc[(industry_inner, event_date)])
            industry_end = float(quote_map.loc[(industry_inner, horizon_date)])
            industry_return = industry_end / industry_start - 1
            rows.append(
                {
                    "signal_month": pd.Timestamp(event["month_end"]).strftime("%Y-%m"),
                    "publish_date": pd.Timestamp(event["publish_date"]).strftime("%Y-%m-%d"),
                    "event_date": event_date,
                    "industry_code": industry["index_code"],
                    "industry_name": industry["index_name"],
                    "excess_return_20d": industry_return - benchmark_return,
                }
            )

    detail = pd.DataFrame(rows)
    if detail.empty:
        raise ValueError("No event return rows were calculated")
    return detail


def format_result(return_detail: pd.DataFrame) -> pd.DataFrame:
    result = return_detail.rename(
        columns={
            "signal_month": "信号月份",
            "publish_date": "公布日期",
            "event_date": "事件日",
            "industry_code": "行业代码",
            "industry_name": "行业名称",
            "excess_return_20d": "20 日超额收益",
        }
    )
    result["事件日"] = pd.to_datetime(result["事件日"]).dt.strftime("%Y-%m-%d")
    result["20 日超额收益"] = result["20 日超额收益"].round(6)
    return result[
        ["信号月份", "公布日期", "事件日", "行业代码", "行业名称", "20 日超额收益"]
    ].sort_values(["信号月份", "行业代码"]).reset_index(drop=True)


def main() -> pd.DataFrame:
    script_dir = Path(__file__).resolve().parent
    output_path = script_dir / "result.csv"

    pmi = fetch_pmi_values()
    signals = build_pmi_signals(pmi)
    events = signals[signals["is_event"]].copy()
    if events.empty:
        raise ValueError("No event month matched the production/order PMI conditions")

    trading_days = fetch_trading_days()
    events = attach_event_dates(events, trading_days)
    indices = fetch_indices()
    quotes = fetch_index_quotes(indices, events)
    return_detail = calculate_event_returns(events, indices, quotes)
    result = format_result(return_detail)

    result.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.6f")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
