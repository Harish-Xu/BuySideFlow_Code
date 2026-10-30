#!/usr/bin/env python3
from __future__ import annotations

import math
import os
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


os.environ.setdefault("DB_CONNECT_TIMEOUT_SEC", "10")

START_DATE = "2024-10-31"
END_DATE = "2025-10-31"
FINAL_EXIT_DATE = "2025-12-31"
CALENDAR_START_DATE = "2024-09-01"
CALENDAR_END_DATE = "2025-12-31"
FINANCIAL_START_DATE = "2020-01-01"
TARGET_MONTHS = ("2024-10", "2025-04", "2025-08", "2025-10")
TOP_N = 20
MIN_LISTING_DAYS = 180
LIQUIDITY_LOOKBACK_DAYS = 20
MIN_AVG_TURNOVER_VALUE = 50_000_000.0
TRANSACTION_COST_RATE = 0.001
COMPANY_BATCH_SIZE = 300
INNER_BATCH_SIZE = 300
MARK_PRIORITY = {1: 5, 8: 4, 7: 3, 6: 2, 2: 1}
# lc_liststatus.CHANGETYPE 1-9 are listing-status events; only 1/3 are active.
ACTIVE_LIST_STATUS_TYPES = {1, 3}

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


def render_sql(name: str, **params: str) -> str:
    if name not in SQL_BLOCKS:
        raise KeyError(f"Missing SQL block: {name}")
    return Template(SQL_BLOCKS[name]).substitute(**params)


def query_block(name: str, timeout: int = 600, **params: str) -> pd.DataFrame:
    return query(render_sql(name, **params), timeout=timeout)


def format_int_list(values: pd.Series | list[int] | np.ndarray) -> str:
    unique_values = sorted(pd.unique(pd.Series(values).dropna()).astype(int))
    if not unique_values:
        raise ValueError("No integer values available for SQL IN clause")
    return ", ".join(str(x) for x in unique_values)


def format_date_literals(values: pd.Series | list[pd.Timestamp] | np.ndarray) -> str:
    dates = pd.to_datetime(pd.Series(values).dropna().tolist())
    unique_dates = sorted({d.strftime("%Y-%m-%d") for d in dates})
    if not unique_dates:
        raise ValueError("No dates available for SQL IN clause")
    return ", ".join(f"DATE('{d}')" for d in unique_dates)


def fetch_month_ends() -> pd.DataFrame:
    month_ends = query_block(
        "month_ends",
        timeout=120,
        start_date=START_DATE,
        end_date=END_DATE,
    )
    return month_ends[month_ends["month_id"].isin(TARGET_MONTHS)].reset_index(drop=True)


def fetch_trading_days() -> pd.DataFrame:
    return query_block(
        "trading_days",
        timeout=120,
        calendar_start_date=CALENDAR_START_DATE,
        calendar_end_date=CALENDAR_END_DATE,
    )


def fetch_component_history() -> pd.DataFrame:
    return query_block(
        "component_history",
        timeout=240,
        start_date=START_DATE,
        end_date=END_DATE,
    )


def fetch_list_status_history() -> pd.DataFrame:
    return query_block(
        "list_status_history",
        timeout=240,
        end_date=END_DATE,
    )


def fetch_risk_alerts() -> pd.DataFrame:
    return query_block(
        "risk_alerts",
        timeout=120,
        start_date=START_DATE,
        end_date=END_DATE,
    )


def fetch_valuation_history(innercodes: pd.Series, obs_dates: pd.Series) -> pd.DataFrame:
    codes = sorted(pd.unique(innercodes.dropna()).astype(int))
    date_values = format_date_literals(obs_dates)
    parts: list[pd.DataFrame] = []
    total = math.ceil(len(codes) / INNER_BATCH_SIZE)
    for batch_idx, i in enumerate(range(0, len(codes), INNER_BATCH_SIZE), start=1):
        print(f"valuation_batch={batch_idx}/{total}", flush=True)
        parts.append(
            query_block(
                "valuation_history",
                timeout=180,
                innercode_values=format_int_list(codes[i : i + INNER_BATCH_SIZE]),
                date_values=date_values,
            )
        )
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def fetch_financial_cashflow(companycodes: pd.Series) -> pd.DataFrame:
    codes = sorted(pd.unique(companycodes.dropna()).astype(int))
    parts: list[pd.DataFrame] = []
    total = math.ceil(len(codes) / COMPANY_BATCH_SIZE)
    for batch_idx, i in enumerate(range(0, len(codes), COMPANY_BATCH_SIZE), start=1):
        print(f"cashflow_batch={batch_idx}/{total}", flush=True)
        parts.append(
            query_block(
                "financial_cashflow",
                timeout=240,
                company_values=format_int_list(codes[i : i + COMPANY_BATCH_SIZE]),
                financial_start_date=FINANCIAL_START_DATE,
                end_date=END_DATE,
            )
        )
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def fetch_dividend_history(innercodes: pd.Series) -> pd.DataFrame:
    codes = sorted(pd.unique(innercodes.dropna()).astype(int))
    parts: list[pd.DataFrame] = []
    total = math.ceil(len(codes) / INNER_BATCH_SIZE)
    for batch_idx, i in enumerate(range(0, len(codes), INNER_BATCH_SIZE), start=1):
        print(f"dividend_batch={batch_idx}/{total}", flush=True)
        parts.append(
            query_block(
                "dividend_history",
                timeout=240,
                innercode_values=format_int_list(codes[i : i + INNER_BATCH_SIZE]),
                financial_start_date=FINANCIAL_START_DATE,
                end_date=END_DATE,
            )
        )
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def fetch_liquidity_quotes(innercodes: pd.Series, dates: pd.Series) -> pd.DataFrame:
    codes = sorted(pd.unique(innercodes.dropna()).astype(int))
    date_values = format_date_literals(dates)
    parts: list[pd.DataFrame] = []
    total = math.ceil(len(codes) / INNER_BATCH_SIZE)
    for batch_idx, i in enumerate(range(0, len(codes), INNER_BATCH_SIZE), start=1):
        print(f"liquidity_batch={batch_idx}/{total}", flush=True)
        parts.append(
            query_block(
                "liquidity_quotes",
                timeout=240,
                innercode_values=format_int_list(codes[i : i + INNER_BATCH_SIZE]),
                date_values=date_values,
            )
        )
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def fetch_stock_quotes(innercodes: pd.Series, dates: pd.Series) -> pd.DataFrame:
    codes = sorted(pd.unique(innercodes.dropna()).astype(int))
    date_values = format_date_literals(dates)
    parts: list[pd.DataFrame] = []
    total = math.ceil(len(codes) / INNER_BATCH_SIZE)
    for batch_idx, i in enumerate(range(0, len(codes), INNER_BATCH_SIZE), start=1):
        print(f"quote_batch={batch_idx}/{total}", flush=True)
        parts.append(
            query_block(
                "stock_quotes",
                timeout=240,
                innercode_values=format_int_list(codes[i : i + INNER_BATCH_SIZE]),
                date_values=date_values,
            )
        )
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def fetch_benchmark_quotes(dates: pd.Series) -> pd.DataFrame:
    return query_block(
        "benchmark_quotes",
        timeout=180,
        date_values=format_date_literals(dates),
    )


def build_component_snapshots(
    month_ends: pd.DataFrame,
    trading_days: pd.DataFrame,
    component_history: pd.DataFrame,
    list_status_history: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    trade_dates = pd.Series(pd.to_datetime(trading_days["trade_date"]).sort_values().unique())
    if trade_dates.empty:
        raise ValueError("Trading calendar is empty")

    history = component_history.copy()
    history["INNERCODE"] = pd.to_numeric(history["INNERCODE"], errors="coerce").astype(int)
    history["COMPANYCODE"] = pd.to_numeric(history["COMPANYCODE"], errors="coerce").astype(int)
    history["listed_date"] = pd.to_datetime(history["listed_date"])
    history["in_date"] = pd.to_datetime(history["in_date"])
    history["out_date"] = pd.to_datetime(history["out_date"])

    status = list_status_history.copy()
    if status.empty:
        raise ValueError("No listing-status events found")
    status["INNERCODE"] = pd.to_numeric(status["INNERCODE"], errors="coerce").astype("Int64")
    status["change_type"] = pd.to_numeric(status["change_type"], errors="coerce").astype("Int64")
    status["change_date"] = pd.to_datetime(status["change_date"])
    status["visible_date"] = pd.to_datetime(status["visible_date"])
    for col in ["JSID", "ID"]:
        status[col] = pd.to_numeric(status[col], errors="coerce")
    status = status.dropna(subset=["INNERCODE", "change_type", "change_date"])

    schedule_rows: list[dict[str, object]] = []
    snapshot_rows: list[pd.DataFrame] = []
    for obs_date in pd.to_datetime(month_ends["obs_date"]).sort_values():
        pos = trade_dates.searchsorted(obs_date)
        if pos >= len(trade_dates) or trade_dates.iloc[pos] != obs_date:
            continue
        entry_pos = pos + 1
        if entry_pos >= len(trade_dates):
            continue

        active = history[
            (history["in_date"] <= obs_date)
            & (history["out_date"].isna() | (history["out_date"] > obs_date))
            & (history["listed_date"] <= obs_date - pd.Timedelta(days=MIN_LISTING_DAYS))
        ].sort_values(["INNERCODE", "in_date", "out_date"])
        active = active.drop_duplicates("INNERCODE", keep="last")
        status_asof = status[
            (status["change_date"] <= obs_date)
            & (status["visible_date"] <= obs_date)
        ].sort_values(
            ["INNERCODE", "change_date", "visible_date", "JSID", "ID"],
            na_position="last",
        )
        status_asof = status_asof.drop_duplicates("INNERCODE", keep="last")
        active = active.merge(
            status_asof[["INNERCODE", "change_type"]],
            on="INNERCODE",
            how="left",
        )
        change_type = active["change_type"].astype("Int64")
        active = active[
            change_type.isin(ACTIVE_LIST_STATUS_TYPES)
        ].drop(columns=["change_type"])
        if active.empty:
            continue

        schedule_rows.append(
            {
                "obs_date": obs_date,
                "entry_date": pd.Timestamp(trade_dates.iloc[entry_pos]),
                "component_count": int(len(active)),
            }
        )
        snap = active[["INNERCODE", "COMPANYCODE", "SECUCODE", "stock_name", "listed_date"]].copy()
        snap.insert(0, "obs_date", obs_date)
        snapshot_rows.append(snap)

    if not schedule_rows:
        raise ValueError("No effective A500 observation dates found")

    schedule = pd.DataFrame(schedule_rows).sort_values("obs_date").reset_index(drop=True)
    final_exit_candidates = trade_dates[trade_dates <= pd.Timestamp(FINAL_EXIT_DATE)]
    if final_exit_candidates.empty:
        raise ValueError("No final exit trading day found")
    final_exit_date = pd.Timestamp(final_exit_candidates.iloc[-1])
    schedule["exit_date"] = schedule["entry_date"].shift(-1)
    schedule["exit_price"] = "open"
    schedule.loc[schedule.index[-1], "exit_date"] = final_exit_date
    schedule.loc[schedule.index[-1], "exit_price"] = "close"
    snapshots = pd.concat(snapshot_rows, ignore_index=True)
    snapshots["obs_date"] = pd.to_datetime(snapshots["obs_date"])
    return schedule, snapshots


def prepare_cashflow(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    df["COMPANYCODE"] = pd.to_numeric(df["COMPANYCODE"], errors="coerce").astype(int)
    df["end_date"] = pd.to_datetime(df["end_date"])
    df["pub_date"] = pd.to_datetime(df["pub_date"])
    df["MARK"] = pd.to_numeric(df["MARK"], errors="coerce").astype(int)
    df["statement_priority"] = pd.to_numeric(df["statement_priority"], errors="coerce")
    df["accounting_priority"] = pd.to_numeric(df["accounting_priority"], errors="coerce")
    df["NETOPERATECASHFLOW"] = pd.to_numeric(df["NETOPERATECASHFLOW"], errors="coerce")
    df["UPDATETIME"] = pd.to_datetime(df["UPDATETIME"])
    for col in ["JSID", "ID"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["COMPANYCODE", "end_date", "pub_date", "MARK"])


def prepare_dividends(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    df["INNERCODE"] = pd.to_numeric(df["INNERCODE"], errors="coerce").astype(int)
    df["COMPANYCODE"] = pd.to_numeric(df["COMPANYCODE"], errors="coerce").astype(int)
    df["end_date"] = pd.to_datetime(df["end_date"])
    df["pub_date"] = pd.to_datetime(df["pub_date"])
    df["cash_dividend"] = pd.to_numeric(df["cash_dividend"], errors="coerce").fillna(0.0)
    df["XGRQ"] = pd.to_datetime(df["XGRQ"])
    for col in ["JSID", "ID", "IFDIVIDEND"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["COMPANYCODE", "end_date", "pub_date"])


def prepare_valuation(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    df["INNERCODE"] = pd.to_numeric(df["INNERCODE"], errors="coerce").astype(int)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["TOTALMV"] = pd.to_numeric(df["TOTALMV"], errors="coerce")
    return df.dropna(subset=["INNERCODE", "trade_date", "TOTALMV"])


def prepare_risk_alerts(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    if df.empty:
        return df
    df["INNERCODE"] = pd.to_numeric(df["INNERCODE"], errors="coerce").astype("Int64")
    df["implement_date"] = pd.to_datetime(df["implement_date"])
    df["remove_date"] = pd.to_datetime(df["remove_date"])
    df["implement_visible_date"] = pd.to_datetime(df["implement_visible_date"])
    df["remove_visible_date"] = pd.to_datetime(df["remove_visible_date"])
    df["risk_alert_type"] = pd.to_numeric(df["risk_alert_type"], errors="coerce")
    return df.dropna(subset=["INNERCODE", "implement_date", "implement_visible_date"])


def prepare_liquidity(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    df["INNERCODE"] = pd.to_numeric(df["INNERCODE"], errors="coerce").astype(int)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["CLOSEPRICE"] = pd.to_numeric(df["CLOSEPRICE"], errors="coerce")
    df["TURNOVERVALUE"] = pd.to_numeric(df["TURNOVERVALUE"], errors="coerce")
    return df.dropna(subset=["INNERCODE", "trade_date"])


def build_liquidity_windows(schedule: pd.DataFrame, trading_days: pd.DataFrame) -> pd.DataFrame:
    trade_dates = pd.Series(pd.to_datetime(trading_days["trade_date"]).sort_values().unique())
    rows: list[dict[str, pd.Timestamp]] = []
    for obs_date in pd.to_datetime(schedule["obs_date"]):
        pos = trade_dates.searchsorted(obs_date)
        if pos >= len(trade_dates) or trade_dates.iloc[pos] != obs_date:
            continue
        start_pos = pos - LIQUIDITY_LOOKBACK_DAYS + 1
        if start_pos < 0:
            continue
        for trade_date in trade_dates.iloc[start_pos : pos + 1]:
            rows.append({"obs_date": pd.Timestamp(obs_date), "trade_date": pd.Timestamp(trade_date)})
    return pd.DataFrame(rows)


def filter_risk_alerts(snap: pd.DataFrame, risk_alerts: pd.DataFrame, obs_date: pd.Timestamp) -> pd.DataFrame:
    if risk_alerts.empty or snap.empty:
        return snap
    active_alerts = risk_alerts[
        (risk_alerts["implement_visible_date"] <= obs_date)
        & (risk_alerts["implement_date"] <= obs_date)
        & (
            risk_alerts["remove_date"].isna()
            | (risk_alerts["remove_date"] > obs_date)
            | (risk_alerts["remove_visible_date"] > obs_date)
        )
    ]["INNERCODE"]
    return snap[~snap["INNERCODE"].isin(active_alerts)].copy()


def filter_liquidity(
    snap: pd.DataFrame,
    liquidity: pd.DataFrame,
    liquidity_windows: pd.DataFrame,
    obs_date: pd.Timestamp,
) -> pd.DataFrame:
    if snap.empty or liquidity.empty or liquidity_windows.empty:
        return snap.iloc[0:0].copy()
    window_dates = liquidity_windows.loc[
        liquidity_windows["obs_date"].eq(obs_date), "trade_date"
    ]
    if len(window_dates) != LIQUIDITY_LOOKBACK_DAYS:
        return snap.iloc[0:0].copy()
    window = liquidity[
        liquidity["trade_date"].isin(window_dates)
        & liquidity["INNERCODE"].isin(snap["INNERCODE"])
        & liquidity["CLOSEPRICE"].gt(0)
        & liquidity["TURNOVERVALUE"].notna()
    ].copy()
    if window.empty:
        return snap.iloc[0:0].copy()
    avg_turnover = window.groupby("INNERCODE").agg(
        turnover_days=("trade_date", "nunique"),
        avg_turnover_value=("TURNOVERVALUE", "mean"),
    )
    obs_tradable = window[
        window["trade_date"].eq(obs_date) & window["TURNOVERVALUE"].gt(0)
    ][["INNERCODE"]].drop_duplicates()
    eligible = avg_turnover[
        avg_turnover["turnover_days"].eq(LIQUIDITY_LOOKBACK_DAYS)
        & avg_turnover["avg_turnover_value"].ge(MIN_AVG_TURNOVER_VALUE)
    ].reset_index()
    eligible = eligible.merge(obs_tradable, on="INNERCODE", how="inner")
    return snap.merge(eligible[["INNERCODE"]], on="INNERCODE", how="inner")


def add_single_quarter(
    df: pd.DataFrame, value_col: str, output_col: str, group_cols: list[str]
) -> pd.DataFrame:
    out = df.copy()
    out["fiscal_year"] = out["end_date"].dt.year
    out["report_month"] = out["end_date"].dt.month
    out["quarter_no"] = out["report_month"].map({3: 1, 6: 2, 9: 3, 12: 4})
    out["quarter_index"] = out["fiscal_year"] * 4 + out["quarter_no"]
    out = out.sort_values([*group_cols, "end_date"])
    grouped = out.groupby(group_cols, group_keys=False)
    out["prev_value"] = grouped[value_col].shift(1)
    out["prev_fiscal_year"] = grouped["fiscal_year"].shift(1)
    out["prev_report_month"] = grouped["report_month"].shift(1)
    out["prev_quarter_index"] = grouped["quarter_index"].shift(1)
    expected_prev_month = out["report_month"].map({3: np.nan, 6: 3, 9: 6, 12: 9})
    cumulative_months = out["report_month"].isin([6, 9, 12])
    valid_prev_quarter = (
        cumulative_months
        & out["prev_fiscal_year"].eq(out["fiscal_year"])
        & out["prev_report_month"].eq(expected_prev_month)
        & out["quarter_index"].sub(out["prev_quarter_index"]).eq(1)
    )
    out[output_col] = out[value_col]
    out.loc[valid_prev_quarter, output_col] = (
        out.loc[valid_prev_quarter, value_col] - out.loc[valid_prev_quarter, "prev_value"]
    )
    out.loc[cumulative_months & ~valid_prev_quarter, output_col] = np.nan
    return out.drop(
        columns=[
            "prev_value",
            "prev_fiscal_year",
            "prev_report_month",
            "prev_quarter_index",
        ]
    )


def latest_per_period_cashflow(cashflow_raw: pd.DataFrame, obs_date: pd.Timestamp) -> pd.DataFrame:
    asof_exclusive = obs_date + pd.Timedelta(days=1)
    df = cashflow_raw[
        (cashflow_raw["pub_date"] <= obs_date) & (cashflow_raw["end_date"] <= obs_date)
        & (cashflow_raw["UPDATETIME"] < asof_exclusive)
    ].copy()
    if df.empty:
        return df
    sort_cols = [
        "COMPANYCODE",
        "MARK",
        "end_date",
        "accounting_priority",
        "pub_date",
        "UPDATETIME",
        "JSID",
        "ID",
    ]
    return (
        df.sort_values(sort_cols)
        .drop_duplicates(["COMPANYCODE", "MARK", "end_date"], keep="last")
        .reset_index(drop=True)
    )


def build_cashflow_metrics(cashflow_raw: pd.DataFrame, obs_date: pd.Timestamp) -> pd.DataFrame:
    cf = latest_per_period_cashflow(cashflow_raw, obs_date)
    if cf.empty:
        return pd.DataFrame(columns=["COMPANYCODE", "ocf_pos_years_3y", "ocf_sum_4q"])

    cf = add_single_quarter(cf, "NETOPERATECASHFLOW", "single_q_ocf", ["COMPANYCODE", "MARK"])
    grouped = cf.groupby(["COMPANYCODE", "MARK"], group_keys=False)

    annual = cf[cf["report_month"] == 12].copy()
    annual["year_span_3"] = annual.groupby(["COMPANYCODE", "MARK"])["fiscal_year"].transform(
        lambda s: s - s.shift(2)
    )
    annual["ocf_pos_years_3y"] = annual.groupby(["COMPANYCODE", "MARK"])[
        "NETOPERATECASHFLOW"
    ].transform(lambda s: s.gt(0).astype(float).rolling(3, min_periods=3).sum())
    annual.loc[annual["year_span_3"] != 2, "ocf_pos_years_3y"] = np.nan
    annual_metrics = (
        annual.dropna(subset=["ocf_pos_years_3y"])
        .sort_values(["COMPANYCODE", "MARK", "end_date"])
        .drop_duplicates(["COMPANYCODE", "MARK"], keep="last")
        [["COMPANYCODE", "MARK", "statement_priority", "end_date", "ocf_pos_years_3y"]]
        .rename(columns={"end_date": "ocf_end_date"})
    )

    cf["lag3_quarter_index"] = grouped["quarter_index"].shift(3)
    cf["ocf_sum_4q"] = grouped["single_q_ocf"].transform(
        lambda s: s.rolling(4, min_periods=4).sum()
    )
    cf.loc[~cf["quarter_index"].sub(cf["lag3_quarter_index"]).eq(3), "ocf_sum_4q"] = np.nan
    q_metrics = (
        cf.dropna(subset=["ocf_sum_4q"])
        .sort_values(["COMPANYCODE", "MARK", "end_date"])
        .drop_duplicates(["COMPANYCODE", "MARK"], keep="last")
        [["COMPANYCODE", "MARK", "end_date", "ocf_sum_4q"]]
        .rename(columns={"end_date": "q_end_date"})
    )

    metrics = annual_metrics.merge(q_metrics, on=["COMPANYCODE", "MARK"], how="inner")
    if metrics.empty:
        return pd.DataFrame(columns=["COMPANYCODE", "ocf_pos_years_3y", "ocf_sum_4q"])
    metrics = metrics.sort_values(
        ["COMPANYCODE", "q_end_date", "ocf_end_date", "statement_priority"],
        ascending=[True, False, False, False],
    )
    return metrics.drop_duplicates("COMPANYCODE", keep="first")[
        ["COMPANYCODE", "ocf_pos_years_3y", "ocf_sum_4q"]
    ]


def build_dividend_metrics(dividend_raw: pd.DataFrame, obs_date: pd.Timestamp) -> pd.DataFrame:
    asof_exclusive = obs_date + pd.Timedelta(days=1)
    df = dividend_raw[
        (dividend_raw["pub_date"] <= obs_date) & (dividend_raw["end_date"] <= obs_date)
        & (dividend_raw["XGRQ"].isna() | (dividend_raw["XGRQ"] < asof_exclusive))
    ].copy()
    if df.empty:
        return pd.DataFrame(columns=["COMPANYCODE", "dividend_pos_years_3y"])
    df["fiscal_year"] = df["end_date"].dt.year
    df = (
        df.sort_values(["COMPANYCODE", "fiscal_year", "pub_date", "XGRQ", "JSID", "ID"])
        .drop_duplicates(["COMPANYCODE", "fiscal_year"], keep="last")
        .sort_values(["COMPANYCODE", "fiscal_year"])
        .reset_index(drop=True)
    )
    grouped = df.groupby("COMPANYCODE", group_keys=False)
    df["year_span_3"] = grouped["fiscal_year"].transform(lambda s: s - s.shift(2))
    df["dividend_pos"] = df["cash_dividend"].gt(0).astype(float)
    df["dividend_pos_years_3y"] = grouped["dividend_pos"].transform(
        lambda s: s.rolling(3, min_periods=3).sum()
    )
    df.loc[df["year_span_3"] != 2, "dividend_pos_years_3y"] = np.nan
    return (
        df.dropna(subset=["dividend_pos_years_3y"])
        .sort_values(["COMPANYCODE", "fiscal_year"])
        .drop_duplicates("COMPANYCODE", keep="last")
        [["COMPANYCODE", "dividend_pos_years_3y"]]
    )


def build_holdings(
    schedule: pd.DataFrame,
    component_snapshots: pd.DataFrame,
    valuation_history: pd.DataFrame,
    risk_alerts: pd.DataFrame,
    liquidity_quotes: pd.DataFrame,
    liquidity_windows: pd.DataFrame,
    cashflow_raw: pd.DataFrame,
    dividend_raw: pd.DataFrame,
) -> pd.DataFrame:
    valuation = prepare_valuation(valuation_history)
    risk_alerts = prepare_risk_alerts(risk_alerts)
    liquidity = prepare_liquidity(liquidity_quotes)
    rows: list[pd.DataFrame] = []
    factor_cols = [
        "ocf_pos_years_3y",
        "ocf_to_mv_4q",
        "dividend_pos_years_3y",
    ]

    for sched_row in schedule.itertuples(index=False):
        obs_date = pd.Timestamp(sched_row.obs_date)
        snap = component_snapshots[component_snapshots["obs_date"] == obs_date].copy()
        if snap.empty:
            continue
        snap = filter_risk_alerts(snap, risk_alerts, obs_date)
        snap = filter_liquidity(snap, liquidity, liquidity_windows, obs_date)
        if snap.empty:
            continue

        val = valuation[valuation["trade_date"] == obs_date][["INNERCODE", "TOTALMV"]].copy()
        metrics = build_cashflow_metrics(cashflow_raw, obs_date)
        dividend_metrics = build_dividend_metrics(dividend_raw, obs_date)

        snap = snap.merge(val, on="INNERCODE", how="inner")
        snap = snap.merge(metrics, on="COMPANYCODE", how="left")
        snap = snap.merge(dividend_metrics, on="COMPANYCODE", how="left")
        snap["ocf_to_mv_4q"] = snap["ocf_sum_4q"] / snap["TOTALMV"]

        eligible = snap.dropna(subset=factor_cols).copy()
        if eligible.empty:
            continue
        for col in factor_cols:
            eligible[f"{col}_pct"] = eligible[col].rank(method="average", pct=True)
        eligible["score"] = eligible[[f"{col}_pct" for col in factor_cols]].mean(axis=1)
        eligible = eligible.sort_values(["score", "SECUCODE"], ascending=[False, True]).head(TOP_N)
        if len(eligible) < TOP_N:
            continue
        eligible = eligible.copy()
        eligible["position_rank"] = np.arange(1, len(eligible) + 1)
        eligible["weight"] = 1.0 / len(eligible)
        eligible["entry_date"] = pd.Timestamp(sched_row.entry_date)
        eligible["exit_date"] = pd.Timestamp(sched_row.exit_date)
        eligible["exit_price"] = str(sched_row.exit_price)
        eligible["component_count"] = int(sched_row.component_count)
        eligible["eligible_count"] = int(len(snap.dropna(subset=factor_cols)))
        rows.append(eligible)

    if not rows:
        raise ValueError("No holdings could be built from the factor definition")
    return pd.concat(rows, ignore_index=True).sort_values(["obs_date", "position_rank"])


def build_stock_price_frame(stock_quotes: pd.DataFrame) -> pd.DataFrame:
    prices = stock_quotes.copy()
    prices["INNERCODE"] = pd.to_numeric(prices["INNERCODE"], errors="coerce").astype(int)
    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    prices["OPENPRICE"] = pd.to_numeric(prices["OPENPRICE"], errors="coerce")
    prices["CLOSEPRICE"] = pd.to_numeric(prices["CLOSEPRICE"], errors="coerce")
    prices["BACKWARDPRICE"] = pd.to_numeric(prices["BACKWARDPRICE"], errors="coerce")
    prices = prices.sort_values(["INNERCODE", "trade_date"]).drop_duplicates(
        ["INNERCODE", "trade_date"], keep="last"
    )
    prices["adj_open"] = np.where(
        prices["OPENPRICE"].gt(0) & prices["CLOSEPRICE"].gt(0) & prices["BACKWARDPRICE"].gt(0),
        prices["OPENPRICE"] * prices["BACKWARDPRICE"] / prices["CLOSEPRICE"],
        np.nan,
    )
    prices["adj_close"] = np.where(prices["BACKWARDPRICE"].gt(0), prices["BACKWARDPRICE"], np.nan)
    return prices[["INNERCODE", "trade_date", "adj_open", "adj_close"]]


def build_benchmark_price_frame(benchmark_quotes: pd.DataFrame) -> pd.DataFrame:
    bench = benchmark_quotes.copy()
    bench["trade_date"] = pd.to_datetime(bench["trade_date"])
    bench["OPENPRICE"] = pd.to_numeric(bench["OPENPRICE"], errors="coerce")
    bench["CLOSEPRICE"] = pd.to_numeric(bench["CLOSEPRICE"], errors="coerce")
    bench = bench.sort_values("trade_date").drop_duplicates("trade_date", keep="last")
    return bench.rename(columns={"OPENPRICE": "open_price", "CLOSEPRICE": "close_price"})[
        ["trade_date", "open_price", "close_price"]
    ]


def build_backtest_outputs(
    holdings: pd.DataFrame, stock_prices: pd.DataFrame, benchmark_prices: pd.DataFrame
) -> pd.DataFrame:
    stock_map = stock_prices.set_index(["INNERCODE", "trade_date"]).sort_index()
    benchmark_map = benchmark_prices.set_index("trade_date").sort_index()
    rows: list[dict[str, object]] = []
    previous_weights: dict[int, float] = {}

    for obs_date, current in holdings.groupby("obs_date", sort=True):
        current = current.copy()
        entry_date = pd.Timestamp(current["entry_date"].iloc[0])
        exit_date = pd.Timestamp(current["exit_date"].iloc[0])
        exit_price_type = str(current["exit_price"].iloc[0])
        stock_exit_col = "adj_close" if exit_price_type == "close" else "adj_open"
        bench_exit_col = "close_price" if exit_price_type == "close" else "open_price"
        stock_returns: dict[int, float] = {}
        complete = True
        for row in current.itertuples(index=False):
            inner_code = int(row.INNERCODE)
            entry_key = (inner_code, entry_date)
            exit_key = (inner_code, exit_date)
            if entry_key not in stock_map.index or exit_key not in stock_map.index:
                complete = False
                break
            entry_px = stock_map.at[entry_key, "adj_open"]
            exit_px = stock_map.at[exit_key, stock_exit_col]
            if pd.isna(entry_px) or pd.isna(exit_px) or entry_px <= 0 or exit_px <= 0:
                complete = False
                break
            stock_returns[inner_code] = float(exit_px / entry_px - 1.0)

        if not complete or len(stock_returns) != len(current):
            print(f"skip_incomplete_period={pd.Timestamp(obs_date):%Y-%m-%d}", flush=True)
            continue
        if entry_date not in benchmark_map.index or exit_date not in benchmark_map.index:
            print(f"skip_missing_benchmark={pd.Timestamp(obs_date):%Y-%m-%d}", flush=True)
            continue
        bench_entry = benchmark_map.at[entry_date, "open_price"]
        bench_exit = benchmark_map.at[exit_date, bench_exit_col]
        if pd.isna(bench_entry) or pd.isna(bench_exit) or bench_entry <= 0 or bench_exit <= 0:
            print(f"skip_invalid_benchmark={pd.Timestamp(obs_date):%Y-%m-%d}", flush=True)
            continue

        target_weights = {
            int(row.INNERCODE): float(row.weight)
            for row in current.itertuples(index=False)
        }
        trade_notional = sum(
            abs(target_weights.get(code, 0.0) - previous_weights.get(code, 0.0))
            for code in set(target_weights) | set(previous_weights)
        )
        trade_cost = trade_notional * TRANSACTION_COST_RATE
        gross_portfolio_return = sum(
            target_weights[code] * stock_returns[code] for code in target_weights
        )
        portfolio_return = (1.0 - trade_cost) * (1.0 + gross_portfolio_return) - 1.0
        ending_value = sum(
            target_weights[code] * (1.0 + stock_returns[code]) for code in target_weights
        )
        previous_weights = (
            {
                code: target_weights[code] * (1.0 + stock_returns[code]) / ending_value
                for code in target_weights
            }
            if ending_value > 0
            else {}
        )
        benchmark_return = float(bench_exit / bench_entry - 1.0)
        rows.append(
            {
                "obs_date": pd.Timestamp(obs_date),
                "entry_date": entry_date,
                "exit_date": exit_date,
                "holding_count": int(len(current)),
                "trade_notional": trade_notional,
                "trade_cost": trade_cost,
                "portfolio_return": portfolio_return,
                "benchmark_return": benchmark_return,
                "excess_return": portfolio_return - benchmark_return,
            }
        )

    return pd.DataFrame(rows)


def format_pct(value: object) -> str:
    if pd.isna(value):
        return ""
    return f"{float(value) * 100.0:.2f}"


def assemble_result(period_returns: pd.DataFrame) -> pd.DataFrame:
    if period_returns.empty:
        return pd.DataFrame(
            columns=[
                "观察日",
                "组合收益（%）",
                "基准收益（%）",
                "超额收益（%）",
            ]
        )

    return pd.DataFrame(
        {
            "观察日": pd.to_datetime(period_returns["obs_date"]).dt.strftime("%Y-%m-%d"),
            "组合收益（%）": period_returns["portfolio_return"].map(format_pct),
            "基准收益（%）": period_returns["benchmark_return"].map(format_pct),
            "超额收益（%）": period_returns["excess_return"].map(format_pct),
        }
    )


def main() -> pd.DataFrame:
    output_path = Path(__file__).with_name("result.csv")
    month_ends = fetch_month_ends()
    trading_days = fetch_trading_days()
    component_history = fetch_component_history()
    list_status_history = fetch_list_status_history()
    risk_alerts = fetch_risk_alerts()
    schedule, component_snapshots = build_component_snapshots(
        month_ends, trading_days, component_history, list_status_history
    )
    print(f"schedule_rows={len(schedule)}, component_rows={len(component_snapshots)}")

    companycodes = component_snapshots["COMPANYCODE"]
    innercodes = component_snapshots["INNERCODE"]
    liquidity_windows = build_liquidity_windows(schedule, trading_days)
    valuation_history = fetch_valuation_history(innercodes, schedule["obs_date"])
    liquidity_quotes = fetch_liquidity_quotes(innercodes, liquidity_windows["trade_date"])
    cashflow_raw = prepare_cashflow(fetch_financial_cashflow(companycodes))
    dividend_raw = prepare_dividends(fetch_dividend_history(innercodes))
    print(
        f"valuation_rows={len(valuation_history)}, liquidity_rows={len(liquidity_quotes)}, "
        f"cashflow_rows={len(cashflow_raw)}, dividend_rows={len(dividend_raw)}"
    )

    holdings = build_holdings(
        schedule,
        component_snapshots,
        valuation_history,
        risk_alerts,
        liquidity_quotes,
        liquidity_windows,
        cashflow_raw,
        dividend_raw,
    )
    print(f"holding_rows={len(holdings)}")

    required_dates = pd.concat([holdings["entry_date"], holdings["exit_date"]], ignore_index=True)
    stock_quotes = fetch_stock_quotes(holdings["INNERCODE"], required_dates)
    benchmark_quotes = fetch_benchmark_quotes(required_dates)
    stock_prices = build_stock_price_frame(stock_quotes)
    benchmark_prices = build_benchmark_price_frame(benchmark_quotes)
    period_returns = build_backtest_outputs(holdings, stock_prices, benchmark_prices)
    result = assemble_result(period_returns)
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
