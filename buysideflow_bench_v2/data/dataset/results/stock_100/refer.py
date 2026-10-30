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


SAMPLE_START_DATE = "2025-01-01"
SAMPLE_END_DATE = "2025-12-31"
FETCH_START_DATE = "2024-01-01"
FETCH_END_DATE = "2026-01-31"
BENCHMARK_CODE = "000300"
MOMENTUM_WINDOWS = (5, 20)
FORWARD_HORIZON = 20
COOLDOWN_DAYS = 20
TOP_FRACTION = 0.2
SQL_BLOCK_RE = re.compile(
    r"^-- name:\s*(?P<name>[a-zA-Z0-9_]+)\s*$\n(?P<sql>.*?)(?=^-- name:|\Z)",
    re.M | re.S,
)


def query(sql: str, timeout: int = 600) -> pd.DataFrame:
    df = query_to_dataframe(sql, max_rows=0, timeout=timeout)
    if df is None:
        raise RuntimeError("SQL returned None")
    return df


def load_sql_blocks(sql_text: str | None = None) -> dict[str, str]:
    if not sql_text:
        sql_path = Path(__file__).with_name("refer.sql")
        sql_text = sql_path.read_text(encoding="utf-8")
    else:
        sql_path = "injected sql_code"
    blocks = {
        match.group("name"): match.group("sql").strip()
        for match in SQL_BLOCK_RE.finditer(sql_text)
    }
    if not blocks:
        raise ValueError(f"No named SQL blocks found in {sql_path}")
    return blocks


def render_sql(name: str, **params: str) -> str:
    blocks = load_sql_blocks(globals().get("sql_code"))
    if name not in blocks:
        raise KeyError(f"Missing SQL block: {name}")
    return Template(blocks[name]).substitute(**params)


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


def cross_sectional_zscore(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    valid = values.dropna()
    if valid.empty:
        return pd.Series(np.nan, index=series.index)
    std = valid.std(ddof=0)
    if pd.isna(std) or std == 0:
        result = pd.Series(np.nan, index=series.index)
        result.loc[valid.index] = 0.0
        return result
    result = (values - valid.mean()) / std
    return result


def apply_cooldown(raw_signal: pd.Series, cooldown_days: int = COOLDOWN_DAYS) -> pd.Series:
    signal = raw_signal.fillna(False).astype(bool).to_numpy()
    kept = np.zeros(len(signal), dtype=bool)
    last_kept_idx = -10**9
    for idx, is_signal in enumerate(signal):
        if is_signal and idx - last_kept_idx > cooldown_days:
            kept[idx] = True
            last_kept_idx = idx
    return pd.Series(kept, index=raw_signal.index)


def build_benchmark_features(index_history: pd.DataFrame) -> pd.DataFrame:
    benchmark = index_history.loc[index_history["index_type"] == "benchmark"].copy()
    benchmark["trade_date"] = pd.to_datetime(benchmark["trade_date"])
    benchmark["close_price"] = pd.to_numeric(benchmark["close_price"], errors="coerce")
    benchmark = benchmark.sort_values("trade_date").reset_index(drop=True)

    for window in MOMENTUM_WINDOWS:
        benchmark[f"benchmark_trailing_{window}d_return"] = (
            benchmark["close_price"] / benchmark["close_price"].shift(window) - 1.0
        )
    benchmark["benchmark_forward_20d_return"] = (
        benchmark["close_price"].shift(-FORWARD_HORIZON) / benchmark["close_price"] - 1.0
    )
    complete = benchmark["close_price"].rolling(FORWARD_HORIZON + 1).count().shift(-FORWARD_HORIZON).eq(FORWARD_HORIZON + 1)
    benchmark.loc[~complete, "benchmark_forward_20d_return"] = np.nan
    return benchmark[
        ["trade_date"]
        + [f"benchmark_trailing_{window}d_return" for window in MOMENTUM_WINDOWS]
        + ["benchmark_forward_20d_return"]
    ]


def build_daily_factor_frame(
    index_history: pd.DataFrame, benchmark_features: pd.DataFrame
) -> pd.DataFrame:
    industry = index_history.loc[index_history["index_type"] == "industry"].copy()
    industry["trade_date"] = pd.to_datetime(industry["trade_date"])
    industry["close_price"] = pd.to_numeric(industry["close_price"], errors="coerce")
    industry = industry.sort_values(["SECUCODE", "trade_date"]).reset_index(drop=True)

    benchmark_map = benchmark_features.set_index("trade_date")
    frames: list[pd.DataFrame] = []
    for secucode, group in industry.groupby("SECUCODE", sort=True):
        g = group.sort_values("trade_date").reset_index(drop=True).copy()
        for window in MOMENTUM_WINDOWS:
            g[f"industry_trailing_{window}d_return"] = (
                g["close_price"] / g["close_price"].shift(window) - 1.0
            )

        aligned_benchmark = benchmark_map.reindex(g["trade_date"])
        for window in MOMENTUM_WINDOWS:
            g[f"benchmark_trailing_{window}d_return"] = aligned_benchmark[
                f"benchmark_trailing_{window}d_return"
            ].to_numpy()
            g[f"excess_{window}d"] = (
                g[f"industry_trailing_{window}d_return"] - g[f"benchmark_trailing_{window}d_return"]
            )
        g["benchmark_forward_20d_return"] = aligned_benchmark[
            "benchmark_forward_20d_return"
        ].to_numpy()
        g["industry_forward_20d_return"] = (
            g["close_price"].shift(-FORWARD_HORIZON) / g["close_price"] - 1.0
        )
        complete = g["close_price"].rolling(FORWARD_HORIZON + 1).count().shift(-FORWARD_HORIZON).eq(FORWARD_HORIZON + 1)
        g.loc[~complete, "industry_forward_20d_return"] = np.nan
        g["forward_excess_20d"] = (
            g["industry_forward_20d_return"] - g["benchmark_forward_20d_return"]
        )
        frames.append(g)

    full = pd.concat(frames, ignore_index=True)
    full = full.sort_values(["trade_date", "SECUCODE"]).reset_index(drop=True)

    for window in MOMENTUM_WINDOWS:
        full[f"z_excess_{window}d"] = full.groupby("trade_date")[f"industry_trailing_{window}d_return"].transform(
            cross_sectional_zscore
        )
    full["strength_score"] = 0.5 * full["z_excess_5d"] + 0.5 * full["z_excess_20d"]
    full["raw_strong_signal"] = False
    for _, group in full.groupby("trade_date", sort=True):
        valid = group.dropna(subset=["strength_score"]).sort_values(
            ["strength_score", "SECUCODE"], ascending=[False, True]
        )
        if valid.empty:
            continue
        cutoff = max(1, math.ceil(len(valid) * TOP_FRACTION))
        full.loc[valid.head(cutoff).index, "raw_strong_signal"] = True

    full = full.sort_values(["SECUCODE", "trade_date"]).reset_index(drop=True)
    full["strong_signal"] = False
    in_sample = full["trade_date"].between(SAMPLE_START_DATE, SAMPLE_END_DATE)
    full.loc[in_sample, "strong_signal"] = full.loc[in_sample].groupby(
        "SECUCODE", group_keys=False
    )["raw_strong_signal"].apply(apply_cooldown)
    return full


def build_event_samples(daily_frame: pd.DataFrame) -> pd.DataFrame:
    df = daily_frame.loc[daily_frame["trade_date"].between(SAMPLE_START_DATE, SAMPLE_END_DATE)].copy()
    required_cols = [
        "strength_score",
        "excess_5d",
        "excess_20d",
        "forward_excess_20d",
    ]
    df = df.dropna(subset=required_cols).copy()
    signals = df.loc[df["strong_signal"]].copy()
    if signals.empty:
        return pd.DataFrame(
            columns=[
                "trade_date",
                "SECUCODE",
                "index_name",
                "excess_5d",
                "excess_20d",
                "strength_score",
                "forward_excess_20d",
            ]
        )
    return signals[
        [
            "trade_date",
            "SECUCODE",
            "index_name",
            "excess_5d",
            "excess_20d",
            "strength_score",
            "forward_excess_20d",
        ]
    ].sort_values(["trade_date", "SECUCODE"]).reset_index(drop=True)


def summarize_events(events: pd.DataFrame) -> pd.DataFrame:
    series = pd.to_numeric(events["forward_excess_20d"], errors="coerce").dropna()
    if series.empty:
        row = {"信号数量": 0, "20日平均超额收益(%)": "", "20日胜率(%)": ""}
    else:
        row = {
            "信号数量": int(series.shape[0]),
            "20日平均超额收益(%)": f"{series.mean() * 100.0:.2f}",
            "20日胜率(%)": f"{(series > 0).mean() * 100.0:.2f}",
        }
    return pd.DataFrame([row])


def main() -> pd.DataFrame:
    output_path = Path(__file__).with_name("result.csv")

    index_history = fetch_index_history()
    benchmark_features = build_benchmark_features(index_history)
    daily_frame = build_daily_factor_frame(index_history, benchmark_features)
    events = build_event_samples(daily_frame)
    result = summarize_events(events)

    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    main()
