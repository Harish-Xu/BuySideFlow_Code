#!/usr/bin/env python3
from __future__ import annotations

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


SAMPLE_START_DATE = "2025-01-01"
SAMPLE_END_DATE = "2025-12-31"
FETCH_START_DATE = "1900-01-01"  # Include each index's first computable event.
FETCH_END_DATE = "2026-02-27"
BENCHMARK_CODE = "000300"
PERCENTILE_WINDOW = 252
PERCENTILE_MIN_OBS = 252
VOLATILITY_WINDOW = 20
FORWARD_HORIZON = 20
COOLDOWN_DAYS = 20
SQL_BLOCK_RE = re.compile(
    r"^-- name:\s*(?P<name>[a-zA-Z0-9_]+)\s*$\n(?P<sql>.*?)(?=^-- name:|\Z)",
    re.M | re.S,
)


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


def render_sql(name: str, **params: str) -> str:
    if name not in SQL_BLOCKS:
        raise KeyError(f"Missing SQL block: {name}")
    return Template(SQL_BLOCKS[name]).substitute(**params)


def query_block(name: str, timeout: int = 600, **params: str) -> pd.DataFrame:
    return query(render_sql(name, **params), timeout=timeout)


def fetch_index_history() -> pd.DataFrame:
    return query_block(
        "all_index_history",
        timeout=1200,
        benchmark_code=BENCHMARK_CODE,
        start_date=FETCH_START_DATE,
        end_date=FETCH_END_DATE,
    )


def fetch_trading_calendar() -> pd.DataFrame:
    calendar = query_block(
        "trading_calendar",
        start_date=FETCH_START_DATE,
        end_date=FETCH_END_DATE,
    )
    if calendar.empty:
        raise ValueError("Trading calendar query returned no rows")
    calendar["trade_date"] = pd.to_datetime(calendar["trade_date"])
    return (
        calendar.drop_duplicates("trade_date")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )


def compute_trailing_percentile(
    series: pd.Series, window_size: int, min_obs: int
) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    output = np.full(len(values), np.nan, dtype=float)
    valid_values = []
    for idx, current in enumerate(values):
        if pd.isna(current):
            continue
        valid_values.append(float(current))
        window = np.asarray(valid_values[-window_size:])
        if len(window) < min_obs:
            continue
        output[idx] = float((window <= current).mean())
    return pd.Series(output, index=series.index, name=f"{series.name}_pct")


def build_benchmark_features(
    index_history: pd.DataFrame, trading_calendar: pd.DataFrame
) -> pd.DataFrame:
    benchmark = index_history.loc[index_history["index_type"] == "benchmark"].copy()
    if benchmark.empty:
        raise ValueError("Benchmark quote query returned no rows")
    benchmark["trade_date"] = pd.to_datetime(benchmark["trade_date"])
    benchmark["close_price"] = pd.to_numeric(benchmark["close_price"], errors="coerce")
    benchmark.loc[~np.isfinite(benchmark["close_price"]) | (benchmark["close_price"] <= 0), "close_price"] = np.nan
    benchmark = (
        benchmark.sort_values("trade_date")
        .drop_duplicates("trade_date", keep="last")[["trade_date", "close_price"]]
    )
    benchmark = trading_calendar[["trade_date"]].merge(
        benchmark, on="trade_date", how="left", validate="one_to_one"
    )

    benchmark["benchmark_forward_20d_return"] = (
        benchmark["close_price"].shift(-FORWARD_HORIZON) / benchmark["close_price"] - 1.0
    )
    return benchmark[["trade_date", "benchmark_forward_20d_return"]]


def apply_cooldown(raw_signal: pd.Series, cooldown_days: int = COOLDOWN_DAYS) -> pd.Series:
    signal = raw_signal.fillna(False).astype(bool).to_numpy()
    kept = np.zeros(len(signal), dtype=bool)
    last_kept_idx = -10**9
    for idx, is_signal in enumerate(signal):
        if is_signal and idx - last_kept_idx > cooldown_days:
            kept[idx] = True
            last_kept_idx = idx
    return pd.Series(kept, index=raw_signal.index)


def build_signal_samples(
    index_history: pd.DataFrame,
    trading_calendar: pd.DataFrame,
    benchmark_features: pd.DataFrame,
) -> pd.DataFrame:
    industry = index_history.loc[index_history["index_type"] == "industry"].copy()
    if industry.empty:
        raise ValueError("Industry index quote query returned no rows")
    industry["trade_date"] = pd.to_datetime(industry["trade_date"])
    industry["close_price"] = pd.to_numeric(industry["close_price"], errors="coerce")
    industry["turnover_value"] = pd.to_numeric(industry["turnover_value"], errors="coerce")
    industry.loc[~np.isfinite(industry["close_price"]) | (industry["close_price"] <= 0), "close_price"] = np.nan
    industry.loc[~np.isfinite(industry["turnover_value"]) | (industry["turnover_value"] < 0), "turnover_value"] = np.nan
    industry = industry.sort_values(["SECUCODE", "trade_date"]).reset_index(drop=True)

    benchmark_map = benchmark_features.set_index("trade_date")
    calendar_index = pd.DatetimeIndex(trading_calendar["trade_date"])
    signal_rows: list[dict[str, object]] = []
    for secucode, group in industry.groupby("SECUCODE", sort=True):
        index_name = group["index_name"].dropna().iloc[-1]
        g = (
            group.sort_values("trade_date")
            .drop_duplicates("trade_date", keep="last")
            .set_index("trade_date")[["close_price", "turnover_value"]]
            .reindex(calendar_index)
            .rename_axis("trade_date")
            .reset_index()
        )
        g["SECUCODE"] = secucode
        g["index_name"] = index_name
        g["daily_return"] = g["close_price"] / g["close_price"].shift(1) - 1.0
        g["volatility_20d"] = (
            g["daily_return"].rolling(VOLATILITY_WINDOW, min_periods=VOLATILITY_WINDOW).std(ddof=0)
        )
        g["turnover_pct"] = compute_trailing_percentile(
            g["turnover_value"],
            window_size=PERCENTILE_WINDOW,
            min_obs=PERCENTILE_MIN_OBS,
        )
        g["volatility_pct"] = compute_trailing_percentile(
            g["volatility_20d"],
            window_size=PERCENTILE_WINDOW,
            min_obs=PERCENTILE_MIN_OBS,
        )
        aligned_benchmark = benchmark_map.reindex(g["trade_date"])
        g["benchmark_forward_20d_return"] = aligned_benchmark[
            "benchmark_forward_20d_return"
        ].to_numpy()
        g["forward_excess_20d"] = (
            g["close_price"].shift(-FORWARD_HORIZON) / g["close_price"] - 1.0
            - g["benchmark_forward_20d_return"]
        )
        g["raw_crowding_signal"] = (g["turnover_pct"] > 0.8) & (g["volatility_pct"] > 0.8)
        g["crowding_signal"] = apply_cooldown(g["raw_crowding_signal"])

        g = g.loc[g["trade_date"].between(SAMPLE_START_DATE, SAMPLE_END_DATE)].copy()
        signal_subset = g.loc[g["crowding_signal"]].copy()
        for _, signal_row in signal_subset.iterrows():
            if pd.isna(signal_row["forward_excess_20d"]):
                continue
            signal_rows.append(
                {
                    "trade_date": signal_row["trade_date"],
                    "SECUCODE": secucode,
                    "index_name": signal_row["index_name"],
                    "turnover_pct": signal_row["turnover_pct"],
                    "volatility_pct": signal_row["volatility_pct"],
                    "volatility_20d": signal_row["volatility_20d"],
                    "forward_excess_20d": signal_row["forward_excess_20d"],
                }
            )

    if not signal_rows:
        return pd.DataFrame(
            columns=[
                "trade_date",
                "SECUCODE",
                "index_name",
                "turnover_pct",
                "volatility_pct",
                "volatility_20d",
                "forward_excess_20d",
            ]
        )
    return pd.DataFrame(signal_rows).sort_values(["trade_date", "SECUCODE"]).reset_index(drop=True)


def summarize_signals(signals: pd.DataFrame) -> pd.DataFrame:
    series = pd.to_numeric(signals["forward_excess_20d"], errors="coerce").dropna()
    if series.empty:
        row = {
            "样本数": 0,
            "平均20日超额收益(%)": np.nan,
            "20日超额收益为负占比(%)": np.nan,
        }
    else:
        row = {
            "样本数": int(series.shape[0]),
            "平均20日超额收益(%)": round(float(series.mean() * 100.0), 2),
            "20日超额收益为负占比(%)": round(
                float((series < 0).mean() * 100.0), 2
            ),
        }
    return pd.DataFrame([row])


def main() -> pd.DataFrame:
    output_path = Path(__file__).with_name("result.csv")

    index_history = fetch_index_history()
    trading_calendar = fetch_trading_calendar()
    benchmark_features = build_benchmark_features(index_history, trading_calendar)
    signals = build_signal_samples(index_history, trading_calendar, benchmark_features)
    result = summarize_signals(signals)

    result.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    main()
