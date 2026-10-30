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
INDEX_CODE = "000852"
FETCH_START_DATE = "2023-11-01"
FETCH_END_DATE = "2026-02-27"
DRAWDOWN_WINDOW = 10
MA_WINDOW = 20
VOL_WINDOW = 20
VOL_HISTORY_WINDOW = 252
VOL_HISTORY_MIN_OBS = 252
PREPOST_WINDOW = 20
FORWARD_HORIZONS = (5, 10, 20, 40)
ANNUALIZE_FACTOR = math.sqrt(252.0)
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
        "index_history",
        timeout=300,
        index_code=INDEX_CODE,
        start_date=FETCH_START_DATE,
        end_date=FETCH_END_DATE,
    )


def compute_trailing_percentile(
    series: pd.Series, window_size: int, min_obs: int
) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    result = np.full(len(values), np.nan, dtype=float)
    for idx, current in enumerate(values):
        if pd.isna(current):
            continue
        start = max(0, idx - window_size + 1)
        window = values.iloc[start : idx + 1].dropna()
        if len(window) < min_obs:
            continue
        result[idx] = float((window <= current).mean())
    return pd.Series(result, index=series.index, name=f"{series.name}_pct")


def build_signal_frame(index_history: pd.DataFrame) -> pd.DataFrame:
    df = index_history.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    numeric_cols = [
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "turnover_value",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("trade_date").reset_index(drop=True)

    df["daily_return"] = df["close_price"].pct_change()
    df["ma20"] = df["close_price"].rolling(MA_WINDOW).mean()
    df["close_high_10d"] = df["close_price"].rolling(DRAWDOWN_WINDOW).max()
    df["price_drawdown_from_10d_high"] = df["close_price"] / df["close_high_10d"] - 1.0
    df["volatility_20d"] = (
        df["daily_return"].rolling(VOL_WINDOW).std(ddof=0) * ANNUALIZE_FACTOR
    )
    df["volatility_20d_pct_1y"] = compute_trailing_percentile(
        df["volatility_20d"],
        window_size=VOL_HISTORY_WINDOW,
        min_obs=VOL_HISTORY_MIN_OBS,
    )
    df["panic_state"] = (
        (df["price_drawdown_from_10d_high"] <= -0.08)
        & (df["close_price"] < df["ma20"])
        & (df["volatility_20d_pct_1y"] >= 0.8)
    )
    return df


def add_observation_metrics(signal_frame: pd.DataFrame) -> pd.DataFrame:
    df = signal_frame.copy()
    df["sample_month"] = df["trade_date"].dt.strftime("%Y-%m")
    df["in_sample"] = df["trade_date"].between(SAMPLE_START_DATE, SAMPLE_END_DATE)
    df["prev_panic_state"] = df["panic_state"].shift(1, fill_value=False)
    df["is_event"] = df["in_sample"] & df["panic_state"] & (~df["prev_panic_state"])

    row_pos = np.arange(len(df))
    df["has_full_future_40d"] = row_pos + max(FORWARD_HORIZONS) < len(df)
    df["has_full_pre20d"] = row_pos >= PREPOST_WINDOW
    df["eligible_observation"] = (
        df["in_sample"]
        & df["has_full_future_40d"]
        & df["has_full_pre20d"]
        & df["volatility_20d_pct_1y"].notna()
        & df["turnover_value"].notna()
    )
    df["is_event"] = df["is_event"] & df["eligible_observation"]

    for horizon in FORWARD_HORIZONS:
        df[f"ret_{horizon}d"] = df["close_price"].shift(-horizon) / df["close_price"] - 1.0

    pre_vol = []
    post_vol = []
    pre_turnover = []
    post_turnover = []
    for idx in range(len(df)):
        if not df.at[idx, "eligible_observation"]:
            pre_vol.append(np.nan)
            post_vol.append(np.nan)
            pre_turnover.append(np.nan)
            post_turnover.append(np.nan)
            continue
        pre_ret = df["daily_return"].iloc[idx - PREPOST_WINDOW : idx]
        post_ret = df["daily_return"].iloc[idx + 1 : idx + 1 + PREPOST_WINDOW]
        pre_turn = df["turnover_value"].iloc[idx - PREPOST_WINDOW : idx]
        post_turn = df["turnover_value"].iloc[idx + 1 : idx + 1 + PREPOST_WINDOW]

        pre_vol.append(pre_ret.std(ddof=0) * ANNUALIZE_FACTOR)
        post_vol.append(post_ret.std(ddof=0) * ANNUALIZE_FACTOR)
        pre_turnover.append(pre_turn.mean())
        post_turnover.append(post_turn.mean())

    df["pre_vol_20d"] = pre_vol
    df["post_vol_20d"] = post_vol
    df["pre_turnover_20d"] = pre_turnover
    df["post_turnover_20d"] = post_turnover
    df["vol_change_20d"] = df["post_vol_20d"] / df["pre_vol_20d"] - 1.0
    df["turnover_recovery_20d"] = (
        df["post_turnover_20d"] / df["pre_turnover_20d"] - 1.0
    )
    return df


def build_comparison_samples(observation_frame: pd.DataFrame) -> pd.DataFrame:
    df = observation_frame.copy()
    event_months = sorted(df.loc[df["is_event"], "sample_month"].dropna().unique())
    if not event_months:
        return pd.DataFrame(
            columns=[
                "trade_date",
                "sample_month",
                "group_label",
                "ret_5d",
                "ret_10d",
                "ret_20d",
                "ret_40d",
                "vol_change_20d",
                "turnover_recovery_20d",
            ]
        )

    event_rows = df.loc[df["is_event"]].copy()
    event_rows["group_label"] = "event"

    control_rows = df.loc[
        df["eligible_observation"]
        & (~df["panic_state"])
        & df["sample_month"].isin(event_months)
    ].copy()
    control_rows["group_label"] = "control"

    keep_cols = [
        "trade_date",
        "sample_month",
        "group_label",
        "ret_5d",
        "ret_10d",
        "ret_20d",
        "ret_40d",
        "vol_change_20d",
        "turnover_recovery_20d",
    ]
    samples = pd.concat([event_rows[keep_cols], control_rows[keep_cols]], ignore_index=True)
    return samples.sort_values(["group_label", "trade_date"]).reset_index(drop=True)


def summarize_results(observation_frame: pd.DataFrame, samples: pd.DataFrame) -> pd.DataFrame:
    metric_specs = [
        ("ret_5d", "T+1至T+5区间收益率", "收益率>0", lambda s: s > 0),
        ("ret_10d", "T+1至T+10区间收益率", "收益率>0", lambda s: s > 0),
        ("ret_20d", "T+1至T+20区间收益率", "收益率>0", lambda s: s > 0),
        ("ret_40d", "T+1至T+40区间收益率", "收益率>0", lambda s: s > 0),
        ("vol_change_20d", "后20日实现波动率相对前20日变化", "变化<0", lambda s: s < 0),
        (
            "turnover_recovery_20d",
            "后20日日均成交额相对前20日变化",
            "变化>0",
            lambda s: s > 0,
        ),
    ]

    event_dates = (
        observation_frame.loc[observation_frame["is_event"], "trade_date"]
        .dt.strftime("%Y-%m-%d")
        .tolist()
    )
    matched_months = (
        observation_frame.loc[observation_frame["is_event"], "sample_month"]
        .dropna()
        .tolist()
    )
    event_frame = samples.loc[samples["group_label"] == "event"].copy()
    control_frame = samples.loc[samples["group_label"] == "control"].copy()

    rows: list[dict[str, object]] = []
    for metric_key, metric_name, win_rule, win_func in metric_specs:
        event_series = pd.to_numeric(event_frame[metric_key], errors="coerce").dropna()
        control_series = pd.to_numeric(control_frame[metric_key], errors="coerce").dropna()
        event_mean = event_series.mean() * 100.0 if not event_series.empty else math.nan
        control_mean = control_series.mean() * 100.0 if not control_series.empty else math.nan
        rows.append(
            {
                "event_dates": ";".join(event_dates),
                "matched_months": ";".join(matched_months),
                "matched_month_count": len(set(matched_months)),
                "metric": metric_name,
                "win_rule": win_rule,
                "event_obs_count": int(event_series.shape[0]),
                "control_obs_count": int(control_series.shape[0]),
                "event_mean_pct": event_mean,
                "control_mean_pct": control_mean,
                "event_minus_control_mean_pct": (
                    event_mean - control_mean
                    if pd.notna(event_mean) and pd.notna(control_mean)
                    else math.nan
                ),
                "event_median_pct": (
                    event_series.median() * 100.0 if not event_series.empty else math.nan
                ),
                "control_median_pct": (
                    control_series.median() * 100.0 if not control_series.empty else math.nan
                ),
                "event_win_rate_pct": (
                    win_func(event_series).mean() * 100.0 if not event_series.empty else math.nan
                ),
                "control_win_rate_pct": (
                    win_func(control_series).mean() * 100.0
                    if not control_series.empty
                    else math.nan
                ),
            }
        )

    result = pd.DataFrame(rows)
    numeric_cols = [
        "matched_month_count",
        "event_obs_count",
        "control_obs_count",
        "event_mean_pct",
        "control_mean_pct",
        "event_minus_control_mean_pct",
        "event_median_pct",
        "control_median_pct",
        "event_win_rate_pct",
        "control_win_rate_pct",
    ]
    for col in numeric_cols:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce").round(4)
    return result


def main() -> pd.DataFrame:
    output_path = Path(__file__).with_name("result.csv")

    index_history = fetch_index_history()
    signal_frame = build_signal_frame(index_history)
    observation_frame = add_observation_metrics(signal_frame)
    samples = build_comparison_samples(observation_frame)
    result = summarize_results(observation_frame, samples)

    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    main()
