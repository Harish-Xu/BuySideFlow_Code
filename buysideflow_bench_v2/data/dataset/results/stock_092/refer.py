#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


ANALYSIS_START = pd.Timestamp("2025-01-01")
AS_OF_DATE = pd.Timestamp("2025-12-31")
REG_WINDOW = 18
ZSCORE_WINDOW = 600
BUY_THRESHOLD = 0.7
SELL_THRESHOLD = -0.7


def rolling_beta_r2(window: pd.DataFrame) -> tuple[float, float]:
    x = window["low_price"].to_numpy(dtype=float)
    y = window["high_price"].to_numpy(dtype=float)
    if not (np.isfinite(x).all() and np.isfinite(y).all()) or (x <= 0).any() or (y < x).any():
        return np.nan, np.nan
    x_dev = x - x.mean()
    y_dev = y - y.mean()
    ss_x = float(np.dot(x_dev, x_dev))
    ss_y = float(np.dot(y_dev, y_dev))
    if ss_x == 0:
        return np.nan, np.nan
    cross = float(np.dot(x_dev, y_dev))
    return cross / ss_x, cross**2 / (ss_x * ss_y) if ss_y > 0 else np.nan


def calculate_indicators(price_df: pd.DataFrame) -> pd.DataFrame:
    if price_df.empty:
        raise ValueError("No index quote data returned")

    df = price_df.copy()
    df["trading_day"] = pd.to_datetime(df["trading_day"])
    for column in ["high_price", "low_price", "close_price"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = (
        df.dropna(subset=["trading_day"])
        .sort_values("trading_day")
        .drop_duplicates("trading_day", keep="last")
        .reset_index(drop=True)
    )

    beta = np.full(len(df), np.nan, dtype=float)
    r_squared = np.full(len(df), np.nan, dtype=float)
    for idx in range(REG_WINDOW - 1, len(df)):
        beta[idx], r_squared[idx] = rolling_beta_r2(
            df.iloc[idx + 1 - REG_WINDOW : idx + 1]
        )

    df["beta"] = beta
    df["r_squared"] = r_squared
    valid_beta = df["beta"].replace([np.inf, -np.inf], np.nan).dropna()
    beta_window = valid_beta.rolling(ZSCORE_WINDOW, min_periods=ZSCORE_WINDOW)
    beta_std = beta_window.std(ddof=0).replace(0, np.nan)
    df["zscore"] = ((valid_beta - beta_window.mean()) / beta_std).reindex(df.index)
    df["right_skew_rsrs"] = df["zscore"] * df["r_squared"] * df["beta"]
    target_position = 0
    target_positions: list[int] = []
    for score in df["right_skew_rsrs"]:
        if pd.notna(score):
            if score > BUY_THRESHOLD:
                target_position = 1
            elif score < SELL_THRESHOLD:
                target_position = 0
        target_positions.append(target_position)
    df["target_position"] = target_positions

    if "rebalance_date" in df:
        df["rebalance_date"] = pd.to_datetime(df["rebalance_date"])
    else:
        df["rebalance_date"] = df["trading_day"].shift(-1)
    return df


def build_result(price_df: pd.DataFrame) -> pd.DataFrame:
    df = calculate_indicators(price_df)
    prior_target = df["target_position"].shift(1).fillna(0).astype(int)
    switches = df.loc[
        (df["target_position"] != prior_target)
        & df["trading_day"].between(ANALYSIS_START, AS_OF_DATE)
    ].copy()
    if switches["rebalance_date"].isna().any():
        raise ValueError("Trading calendar does not resolve a signal's next rebalance date")
    result = pd.DataFrame(
        {
            "signal_date": switches["trading_day"].dt.strftime("%Y-%m-%d"),
            "rebalance_date": switches["rebalance_date"].dt.strftime("%Y-%m-%d"),
            "position_after": switches["target_position"].astype(int),
            "right_skew_rsrs": switches["right_skew_rsrs"].round(6),
        }
    ).reset_index(drop=True)
    return result


def main() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    raw_df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if raw_df is None:
        raise ValueError("SQL execution returned None")

    result = build_result(raw_df)
    result.to_csv(
        Path(__file__).with_name("result.csv"),
        index=False,
        encoding="utf-8-sig",
    )
    return result


if __name__ == "__main__":
    result = main()
