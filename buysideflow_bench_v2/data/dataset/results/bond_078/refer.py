#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


EVENT_START = pd.Timestamp("2025-01-01")
EVENT_END = pd.Timestamp("2026-01-01")
HISTORY_START = pd.Timestamp("2024-01-01")
HISTORY_END = pd.Timestamp("2025-01-01")
ZSCORE_THRESHOLD = 2.0
FORWARD_COMMON_DAYS = 10

RESULT_COLUMNS = [
    "背离日",
    "背离方向",
    "起始利差(bp)",
    "起始z-score",
    "10日后利差(bp)",
    "10日后z-score",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="2025 年 5Y FR007 IRS-5Y 国债利差背离及 10 日回归")
    parser.add_argument("--output-path", default=str(Path(__file__).with_name("result.csv")))
    return parser.parse_args()


def load_spread_series() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    if df.empty:
        return pd.DataFrame(columns=["obs_date", "spread_bp"])

    df = df.copy()
    df["obs_date"] = pd.to_datetime(df["obs_date"])
    for col in ["swap_5y_rate_pct", "gov_5y_yield_decimal", "gov_5y_yield_pct", "spread_bp"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return (
        df.dropna(subset=["obs_date", "spread_bp"])
        .sort_values("obs_date")
        .drop_duplicates("obs_date", keep="last")
        .reset_index(drop=True)
    )


def add_history_zscore(spread: pd.DataFrame) -> pd.DataFrame:
    if spread.empty:
        return spread

    data = spread.copy()
    history = data[(data["obs_date"] >= HISTORY_START) & (data["obs_date"] < HISTORY_END)]["spread_bp"]
    history = pd.to_numeric(history, errors="coerce").dropna()
    if len(history) < 2:
        data["history_count"] = len(history)
        data["history_mean_bp"] = np.nan
        data["history_std_bp"] = np.nan
        data["z_score"] = np.nan
        return data

    history_mean = float(history.mean())
    history_std = float(history.std(ddof=0))
    data["history_count"] = int(len(history))
    data["history_mean_bp"] = history_mean
    data["history_std_bp"] = history_std
    data["z_score"] = (data["spread_bp"] - history_mean) / history_std if history_std else np.nan
    return data


def mark_first_triggers(data: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        return data
    marked = data.copy()
    marked["is_trigger"] = marked["z_score"].abs() > ZSCORE_THRESHOLD
    marked["direction"] = np.where(
        marked["z_score"] > ZSCORE_THRESHOLD,
        "IRS相对国债偏高",
        np.where(marked["z_score"] < -ZSCORE_THRESHOLD, "IRS相对国债偏低", ""),
    )
    prev_trigger = marked["is_trigger"].shift(1, fill_value=False).astype(bool)
    prev_direction = marked["direction"].shift(1, fill_value="")
    marked["is_first_trigger"] = marked["is_trigger"] & ~(prev_trigger & (prev_direction == marked["direction"]))
    return marked


def build_events(data: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    marked = mark_first_triggers(data)
    rows: list[dict[str, object]] = []
    for idx, event in marked.iterrows():
        obs_date = pd.Timestamp(event["obs_date"])
        if not (EVENT_START <= obs_date < EVENT_END):
            continue
        if not bool(event["is_first_trigger"]):
            continue

        exit_idx = int(idx) + FORWARD_COMMON_DAYS
        if exit_idx >= len(marked):
            continue
        exit_row = marked.iloc[exit_idx]
        start_z = float(event["z_score"])
        exit_z = float(exit_row["z_score"])
        if np.isnan(exit_z):
            continue

        rows.append(
            {
                "背离日": obs_date.strftime("%Y-%m-%d"),
                "背离方向": event["direction"],
                "起始利差(bp)": float(event["spread_bp"]),
                "起始z-score": start_z,
                "10日后利差(bp)": float(exit_row["spread_bp"]),
                "10日后z-score": exit_z,
            }
        )

    return pd.DataFrame(rows, columns=RESULT_COLUMNS)


def format_result(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    result = df.copy()
    for col in ["起始利差(bp)", "10日后利差(bp)"]:
        result[col] = pd.to_numeric(result[col], errors="coerce").map(lambda value: f"{value:.2f}" if pd.notna(value) else "")
    for col in ["起始z-score", "10日后z-score"]:
        result[col] = pd.to_numeric(result[col], errors="coerce").map(lambda value: f"{value:.4f}" if pd.notna(value) else "")
    return result[RESULT_COLUMNS]


def main() -> None:
    args = parse_args()
    output_path = Path(args.output_path)

    spread = load_spread_series()
    spread = add_history_zscore(spread)
    result = build_events(spread)
    result = format_result(result)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
