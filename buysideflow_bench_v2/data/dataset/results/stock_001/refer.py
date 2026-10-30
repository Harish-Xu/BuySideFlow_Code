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

os.environ.setdefault("DB_CONNECT_TIMEOUT_SEC", "10")

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


START_DATE = "2025-04-01"
END_DATE = "2025-08-31"
FIN_START_DATE = "2020-01-01"
TARGET_MONTHS = ("2025-04", "2025-08")
HORIZON = 20
FINANCIAL_BATCH_SIZE = 1000
QUOTE_BATCH_SIZE = 1000
SQL_BLOCK_RE = re.compile(
    r"^-- name:\s*(?P<name>[a-zA-Z0-9_]+)\s*$\n(?P<sql>.*?)(?=^-- name:|\Z)",
    re.M | re.S,
)


def query(sql: str, timeout: int = 600, retries: int = 2) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            frame = query_to_dataframe(sql, max_rows=0, timeout=timeout)
            if frame is None:
                raise RuntimeError("SQL returned None")
            return frame
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
    blocks = {
        match.group("name"): match.group("sql").strip()
        for match in SQL_BLOCK_RE.finditer(sql_path.read_text(encoding="utf-8"))
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
    return ", ".join(str(value) for value in unique_values)


def format_date_literals(values: pd.Series | list[pd.Timestamp] | np.ndarray) -> str:
    dates = pd.to_datetime([value for value in pd.Series(values).dropna().tolist()])
    unique_dates = sorted({date.strftime("%Y-%m-%d") for date in dates})
    if not unique_dates:
        raise ValueError("No dates available for SQL IN clause")
    return ", ".join(f"DATE('{date}')" for date in unique_dates)


def fetch_month_ends() -> pd.DataFrame:
    month_ends = query_block(
        "month_ends",
        timeout=60,
        start_date=START_DATE,
        end_date=END_DATE,
    )
    month_ends["obs_date"] = pd.to_datetime(month_ends["obs_date"])
    return month_ends[month_ends["month_id"].isin(TARGET_MONTHS)].reset_index(drop=True)


def fetch_trading_days() -> pd.DataFrame:
    trading_days = query_block(
        "trading_days",
        timeout=60,
        start_date=START_DATE,
        end_date=END_DATE,
    )
    trading_days["trade_date"] = pd.to_datetime(trading_days["trade_date"])
    return trading_days


def build_required_dates(
    month_ends: pd.DataFrame, trading_days: pd.DataFrame
) -> pd.DataFrame:
    calendar = trading_days["trade_date"].sort_values().reset_index(drop=True)
    rows: list[dict[str, object]] = []
    for obs_date in month_ends["obs_date"]:
        position = calendar.searchsorted(obs_date)
        future_position = position + HORIZON
        if (
            position >= len(calendar)
            or calendar.iloc[position] != obs_date
            or future_position >= len(calendar)
        ):
            continue
        rows.append(
            {
                "obs_date": obs_date,
                "base_date": obs_date,
                "future_date": calendar.iloc[future_position],
            }
        )
    return pd.DataFrame(rows)


def fetch_universe(month_ends: pd.DataFrame) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for obs_date in month_ends["obs_date"]:
        obs_text = pd.Timestamp(obs_date).strftime("%Y-%m-%d")
        print(f"universe_asof={obs_text}", flush=True)
        part = query_block("universe_asof", timeout=180, obs_date=obs_text)
        if not part.empty:
            parts.append(part)
    if not parts:
        return pd.DataFrame(columns=["obs_date", "INNERCODE", "COMPANYCODE"])
    universe = pd.concat(parts, ignore_index=True)
    universe["obs_date"] = pd.to_datetime(universe["obs_date"])
    for column in ["INNERCODE", "COMPANYCODE"]:
        universe[column] = pd.to_numeric(universe[column], errors="coerce").astype("Int64")
    return (
        universe.dropna(subset=["obs_date", "INNERCODE", "COMPANYCODE"])
        .drop_duplicates(["obs_date", "INNERCODE"])
        .reset_index(drop=True)
    )


def fetch_financials(universe: pd.DataFrame) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for obs_date, snapshot in universe.groupby("obs_date", sort=True):
        codes = sorted(snapshot["COMPANYCODE"].dropna().astype(int).unique())
        total_batches = math.ceil(len(codes) / FINANCIAL_BATCH_SIZE)
        obs_text = pd.Timestamp(obs_date).strftime("%Y-%m-%d")
        for batch_number, start in enumerate(
            range(0, len(codes), FINANCIAL_BATCH_SIZE), start=1
        ):
            print(
                f"financial_asof={obs_text} batch={batch_number}/{total_batches}",
                flush=True,
            )
            part = query_block(
                "financial_asof",
                timeout=180,
                code_values=format_int_list(codes[start : start + FINANCIAL_BATCH_SIZE]),
                fin_start_date=FIN_START_DATE,
                obs_date=obs_text,
            )
            if not part.empty:
                part.insert(0, "obs_date", pd.Timestamp(obs_date))
                parts.append(part)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def fetch_quotes(required_dates: pd.DataFrame, innercodes: pd.Series) -> pd.DataFrame:
    date_values = format_date_literals(
        pd.unique(required_dates[["base_date", "future_date"]].values.ravel("K"))
    )
    codes = sorted(innercodes.dropna().astype(int).unique())
    parts: list[pd.DataFrame] = []
    total_batches = math.ceil(len(codes) / QUOTE_BATCH_SIZE)
    for batch_number, start in enumerate(
        range(0, len(codes), QUOTE_BATCH_SIZE), start=1
    ):
        print(f"quote_batch={batch_number}/{total_batches}", flush=True)
        part = query_block(
            "quotes",
            timeout=120,
            date_values=date_values,
            innercode_values=format_int_list(codes[start : start + QUOTE_BATCH_SIZE]),
        )
        if not part.empty:
            parts.append(part)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def build_feature_panel(financials: pd.DataFrame) -> pd.DataFrame:
    frame = financials.copy()
    frame["end_date"] = pd.to_datetime(frame["end_date"], errors="coerce")
    frame["publish_date"] = pd.to_datetime(frame["publish_date"], errors="coerce")
    numeric_columns = ["COMPANYCODE", "ROETTM"]
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = (
        frame.dropna(subset=["COMPANYCODE", "end_date", "publish_date"])
        .sort_values(["COMPANYCODE", "end_date", "publish_date"])
        .drop_duplicates(["COMPANYCODE", "end_date"], keep="last")
        .sort_values(["COMPANYCODE", "end_date"])
        .reset_index(drop=True)
    )

    frame["fiscal_year"] = frame["end_date"].dt.year
    frame["report_month"] = frame["end_date"].dt.month
    frame["quarter_no"] = frame["report_month"].map({3: 1, 6: 2, 9: 3, 12: 4})
    frame["quarter_index"] = frame["fiscal_year"] * 4 + frame["quarter_no"]

    grouped = frame.groupby("COMPANYCODE", group_keys=False)
    quarter_index = frame["quarter_index"]
    has_8q_window = quarter_index.sub(grouped["quarter_index"].shift(7)).eq(7)
    frame["mean_roe_ttm_8q"] = (
        grouped["ROETTM"].rolling(8, min_periods=8).mean().reset_index(level=0, drop=True)
    )
    frame.loc[~has_8q_window, "mean_roe_ttm_8q"] = np.nan
    latest_period = frame["end_date"].eq(grouped["end_date"].transform("max"))
    features = frame.loc[
        latest_period,
        ["COMPANYCODE", "end_date", "mean_roe_ttm_8q"],
    ]
    return features.dropna(subset=["mean_roe_ttm_8q"]).reset_index(drop=True)


def compute_factor_snapshots(
    universe: pd.DataFrame, financials: pd.DataFrame
) -> pd.DataFrame:
    if financials.empty:
        return pd.DataFrame()
    snapshots: list[pd.DataFrame] = []
    for obs_date, universe_snapshot in universe.groupby("obs_date", sort=True):
        financial_snapshot = financials[financials["obs_date"] == obs_date]
        if financial_snapshot.empty:
            continue
        features = build_feature_panel(financial_snapshot)
        factor = universe_snapshot.merge(features, on="COMPANYCODE", how="inner")
        if factor.empty:
            continue
        factor["factor_score"] = factor["mean_roe_ttm_8q"]
        factor = factor.sort_values(["factor_score", "SECUCODE"], kind="stable").copy()
        n = len(factor)
        sizes = [n // 5 + (group < n % 5) for group in range(5)]
        factor["quantile"] = np.repeat(np.arange(1, 6), sizes)
        snapshots.append(factor)
    return pd.concat(snapshots, ignore_index=True) if snapshots else pd.DataFrame()


def attach_forward_returns(
    factors: pd.DataFrame,
    required_dates: pd.DataFrame,
    quotes: pd.DataFrame,
) -> pd.DataFrame:
    frame = factors.copy()
    frame["future_return_20d"] = np.nan
    if frame.empty or quotes.empty:
        return frame
    frame["obs_date"] = pd.to_datetime(frame["obs_date"])
    schedule = required_dates.copy()
    for column in schedule.columns:
        schedule[column] = pd.to_datetime(schedule[column])
    schedule = schedule.set_index("obs_date")

    prices = quotes.copy()
    prices["INNERCODE"] = pd.to_numeric(prices["INNERCODE"], errors="coerce")
    prices["trade_date"] = pd.to_datetime(prices["trade_date"], errors="coerce")
    prices["adjusted_close"] = pd.to_numeric(prices["adjusted_close"], errors="coerce")
    price_panel = prices.pivot_table(
        index="INNERCODE",
        columns="trade_date",
        values="adjusted_close",
        aggfunc="last",
    )

    parts: list[pd.DataFrame] = []
    for obs_date, snapshot in frame.groupby("obs_date", sort=True):
        if obs_date not in schedule.index:
            continue
        base_date = schedule.loc[obs_date, "base_date"]
        future_date = schedule.loc[obs_date, "future_date"]
        snapshot = snapshot.copy()
        stock_index = pd.Index(snapshot["INNERCODE"].astype(int), name="INNERCODE")
        endpoints = price_panel.reindex(
            index=stock_index, columns=[base_date, future_date]
        )
        base_price = endpoints[base_date].to_numpy()
        future_price = endpoints[future_date].to_numpy()
        snapshot["future_return_20d"] = future_price / base_price - 1
        parts.append(snapshot)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def summarize(
    factor_returns: pd.DataFrame,
    observation_dates: pd.Series | list[pd.Timestamp] | None = None,
) -> pd.DataFrame:
    snapshots = (
        dict(tuple(factor_returns.groupby("obs_date", sort=True)))
        if not factor_returns.empty else {}
    )
    dates = sorted(pd.to_datetime(
        observation_dates if observation_dates is not None else list(snapshots)
    ).unique())
    rows: list[dict[str, object]] = []
    for obs_date in dates:
        snapshot = snapshots.get(pd.Timestamp(obs_date), pd.DataFrame(
            columns=["factor_score", "quantile", "future_return_20d"]
        ))
        valid = snapshot.dropna(
            subset=["factor_score", "quantile", "future_return_20d"]
        )
        rank_ic = np.nan
        if (len(valid) >= 2 and valid["factor_score"].nunique() > 1
                and valid["future_return_20d"].nunique() > 1):
            rank_ic = valid["factor_score"].rank().corr(valid["future_return_20d"].rank())
        q1_return = valid.loc[valid["quantile"] == 1, "future_return_20d"].mean()
        q5_return = valid.loc[valid["quantile"] == 5, "future_return_20d"].mean()
        spread = (q5_return - q1_return) * 100
        rows.append(
            {
                "观察日": pd.Timestamp(obs_date).strftime("%Y-%m-%d"),
                "有效样本数": int(len(valid)),
                "Rank IC": f"{rank_ic:.4f}" if pd.notna(rank_ic) else None,
                "Q5-Q1收益": f"{spread:.2f}" if pd.notna(spread) else None,
            }
        )
    return pd.DataFrame(
        rows,
        columns=["观察日", "有效样本数", "Rank IC", "Q5-Q1收益"],
    )


def main() -> pd.DataFrame:
    output_path = Path(__file__).with_name("result.csv")
    month_ends = fetch_month_ends()
    trading_days = fetch_trading_days()
    required_dates = build_required_dates(month_ends, trading_days)
    if len(required_dates) != len(TARGET_MONTHS):
        raise ValueError("Could not resolve both observation and 20-day forward dates")
    print(f"month_ends={len(month_ends)}, required_dates={len(required_dates)}", flush=True)

    universe = fetch_universe(month_ends)
    print(f"universe_rows={len(universe)}", flush=True)
    financials = fetch_financials(universe)
    print(f"financial_rows={len(financials)}", flush=True)
    factors = compute_factor_snapshots(universe, financials)
    print(f"factor_rows={len(factors)}", flush=True)

    quotes = (fetch_quotes(required_dates, factors["INNERCODE"])
              if not factors.empty else pd.DataFrame())
    print(f"quote_rows={len(quotes)}", flush=True)
    factor_returns = attach_forward_returns(factors, required_dates, quotes)
    result = summarize(factor_returns, required_dates["obs_date"])
    if len(result) != len(TARGET_MONTHS):
        raise ValueError("Expected one result row for each observation date")

    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False), flush=True)
    return result


if __name__ == "__main__":
    main()
