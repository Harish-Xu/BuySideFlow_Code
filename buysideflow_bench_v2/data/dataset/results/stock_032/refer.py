#!/usr/bin/env python3
from __future__ import annotations

import math
import re
import sys
from pathlib import Path
from string import Template

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


SAMPLE_START_DATE = "2024-01-01"
SAMPLE_END_DATE = "2025-08-31"
FETCH_START_DATE = "2022-01-01"
FETCH_END_DATE = "2025-12-31"
BENCHMARK_CODE = "000300"
BREAKOUT_LOOKBACK_DAYS = 250
TURNOVER_LOOKBACK_WEEKS = 60
MA_WINDOW = 20
FORWARD_HORIZONS = (1, 5, 20, 60)
WEEK_FREQ = "W-SUN"
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


def build_benchmark_returns(index_history: pd.DataFrame) -> pd.DataFrame:
    benchmark = index_history.loc[index_history["index_type"] == "benchmark"].copy()
    benchmark["trade_date"] = pd.to_datetime(benchmark["trade_date"])
    benchmark["close_price"] = pd.to_numeric(benchmark["close_price"], errors="coerce")
    benchmark = benchmark.sort_values("trade_date").reset_index(drop=True)

    for horizon in FORWARD_HORIZONS:
        benchmark[f"benchmark_ret_{horizon}d"] = (
            benchmark["close_price"].shift(-horizon) / benchmark["close_price"] - 1.0
        )
    return benchmark[["trade_date"] + [f"benchmark_ret_{h}d" for h in FORWARD_HORIZONS]]


def build_event_frame(index_history: pd.DataFrame) -> pd.DataFrame:
    industry = index_history.loc[index_history["index_type"] == "industry"].copy()
    industry["trade_date"] = pd.to_datetime(industry["trade_date"])
    industry["close_price"] = pd.to_numeric(industry["close_price"], errors="coerce")
    industry["turnover_value"] = pd.to_numeric(industry["turnover_value"], errors="coerce")
    industry = industry.sort_values(["SECUCODE", "trade_date"]).reset_index(drop=True)

    event_rows: list[dict[str, object]] = []
    for secucode, group in industry.groupby("SECUCODE", sort=True):
        g = group.sort_values("trade_date").reset_index(drop=True).copy()
        g["prev_250d_high"] = (
            g["close_price"]
            .shift(1)
            .rolling(BREAKOUT_LOOKBACK_DAYS, min_periods=BREAKOUT_LOOKBACK_DAYS)
            .max()
        )
        g["ma20"] = g["close_price"].rolling(MA_WINDOW, min_periods=MA_WINDOW).mean()
        g["week_id"] = g["trade_date"].dt.to_period(WEEK_FREQ)

        weekly = (
            g.groupby("week_id", as_index=False)
            .agg(
                week_end_date=("trade_date", "max"),
                week_turnover=("turnover_value", "sum"),
            )
            .sort_values("week_end_date")
            .reset_index(drop=True)
        )
        weekly["prev_60w_max_turnover"] = (
            weekly["week_turnover"]
            .shift(1)
            .rolling(TURNOVER_LOOKBACK_WEEKS, min_periods=TURNOVER_LOOKBACK_WEEKS)
            .max()
        )

        g = g.merge(
            weekly[["week_id", "week_end_date", "week_turnover", "prev_60w_max_turnover"]],
            on="week_id",
            how="left",
        )
        event_mask = (
            (g["trade_date"] == g["week_end_date"])
            & g["trade_date"].between(SAMPLE_START_DATE, SAMPLE_END_DATE)
            & g["prev_250d_high"].notna()
            & g["prev_60w_max_turnover"].notna()
            & g["ma20"].notna()
            & (g["close_price"] > g["prev_250d_high"])
            & (g["week_turnover"] > g["prev_60w_max_turnover"])
        )
        event_candidates = g.loc[event_mask].copy()

        for row_idx, event_row in event_candidates.iterrows():
            if row_idx + max(FORWARD_HORIZONS) >= len(g):
                continue
            event_record: dict[str, object] = {
                "SECUCODE": secucode,
                "index_name": event_row["index_name"],
                "trade_date": event_row["trade_date"],
            }
            for horizon in FORWARD_HORIZONS:
                event_record[f"industry_ret_{horizon}d"] = (
                    g.at[row_idx + horizon, "close_price"] / event_row["close_price"] - 1.0
                )

            future_close = g.loc[row_idx + 1 : row_idx + 20, "close_price"]
            event_record["breakout_failure"] = bool((future_close < event_row["ma20"]).any())
            event_rows.append(event_record)

    if not event_rows:
        return pd.DataFrame(
            columns=[
                "SECUCODE",
                "index_name",
                "trade_date",
                "breakout_failure",
            ]
            + [f"industry_ret_{h}d" for h in FORWARD_HORIZONS]
        )
    return pd.DataFrame(event_rows).sort_values(["trade_date", "SECUCODE"]).reset_index(drop=True)


def summarize_results(events: pd.DataFrame, benchmark_returns: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(
            columns=[
                "metric",
                "event_obs_count",
                "unique_industry_index_count",
                "mean_excess_return_pct",
                "win_rate_pct",
                "breakout_failure_ratio_pct",
            ]
        )

    merged = events.merge(benchmark_returns, on="trade_date", how="left")
    for horizon in FORWARD_HORIZONS:
        merged[f"excess_ret_{horizon}d"] = (
            merged[f"industry_ret_{horizon}d"] - merged[f"benchmark_ret_{horizon}d"]
        )

    total_event_count = int(len(merged))
    unique_index_count = int(merged["SECUCODE"].nunique())
    rows: list[dict[str, object]] = []

    for horizon in FORWARD_HORIZONS:
        series = pd.to_numeric(merged[f"excess_ret_{horizon}d"], errors="coerce").dropna()
        rows.append(
            {
                "metric": f"T+{horizon}相对沪深300超额收益",
                "event_obs_count": int(series.shape[0]),
                "unique_industry_index_count": unique_index_count,
                "mean_excess_return_pct": (
                    float(series.mean() * 100.0) if not series.empty else math.nan
                ),
                "win_rate_pct": (
                    float((series > 0).mean() * 100.0) if not series.empty else math.nan
                ),
                "breakout_failure_ratio_pct": math.nan,
            }
        )

    failure_ratio = pd.to_numeric(merged["breakout_failure"], errors="coerce").mean() * 100.0
    rows.append(
        {
            "metric": "breakout_failure_20d",
            "event_obs_count": total_event_count,
            "unique_industry_index_count": unique_index_count,
            "mean_excess_return_pct": math.nan,
            "win_rate_pct": math.nan,
            "breakout_failure_ratio_pct": float(failure_ratio),
        }
    )

    result = pd.DataFrame(rows)
    numeric_cols = [
        "event_obs_count",
        "unique_industry_index_count",
        "mean_excess_return_pct",
        "win_rate_pct",
        "breakout_failure_ratio_pct",
    ]
    for col in numeric_cols:
        result[col] = pd.to_numeric(result[col], errors="coerce").round(4)
    return result


def main() -> pd.DataFrame:
    output_path = Path(__file__).with_name("result.csv")

    index_history = fetch_index_history()
    benchmark_returns = build_benchmark_returns(index_history)
    events = build_event_frame(index_history)
    result = summarize_results(events, benchmark_returns)

    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    main()
