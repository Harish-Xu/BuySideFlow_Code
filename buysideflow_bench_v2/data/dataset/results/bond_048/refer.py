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


SIGNAL_START = pd.Timestamp("2023-01-01")
SIGNAL_END = pd.Timestamp("2024-12-31")
ROLLING_WINDOW = 20
LOW_PERCENTILE = 0.10
HOLDING_DAYS = 20
RESULT_COLUMNS = ["样本数", "平均收益(%)", "中位数收益(%)", "胜率(%)"]


def fmt_pct(value: object) -> str:
    if pd.isna(value):
        return ""
    return f"{float(value) * 100.0:.2f}"


def load_raw_data() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    data = query_to_dataframe(sql, max_rows=0, timeout=600)
    if data is None:
        raise RuntimeError("SQL returned None")
    return data


def prepare_spread(raw: pd.DataFrame) -> pd.DataFrame:
    spread = (
        raw.loc[raw["series_name"].eq("spread_10y_3y"), ["obs_date", "series_value"]]
        .dropna()
        .rename(columns={"obs_date": "signal_date", "series_value": "spread_10y_3y"})
        .copy()
    )
    spread["signal_date"] = pd.to_datetime(spread["signal_date"], errors="coerce")
    spread["spread_10y_3y"] = pd.to_numeric(spread["spread_10y_3y"], errors="coerce")
    spread = (
        spread.dropna(subset=["signal_date", "spread_10y_3y"])
        .sort_values("signal_date")
        .drop_duplicates("signal_date", keep="last")
        .reset_index(drop=True)
    )
    if spread.empty:
        return spread

    spread["rolling_pct_20d"] = (
        spread["spread_10y_3y"]
        .rolling(window=ROLLING_WINDOW, min_periods=ROLLING_WINDOW)
        .apply(lambda window: float(np.mean(window <= window[-1])), raw=True)
    )
    spread["raw_trigger"] = (
        spread["signal_date"].between(SIGNAL_START, SIGNAL_END)
        & (spread["rolling_pct_20d"] < LOW_PERCENTILE)
    )
    # Consecutive curve-trading-day triggers form one episode; keep only the first day.
    spread["is_signal"] = spread["raw_trigger"] & ~spread["raw_trigger"].shift(fill_value=False)
    return spread


def prepare_index_quotes(raw: pd.DataFrame) -> pd.DataFrame:
    quotes = (
        raw.loc[raw["series_name"].eq("index_price"), ["obs_date", "series_value"]]
        .dropna()
        .rename(columns={"obs_date": "trade_date", "series_value": "close_price"})
        .copy()
    )
    quotes["trade_date"] = pd.to_datetime(quotes["trade_date"], errors="coerce")
    quotes["close_price"] = pd.to_numeric(quotes["close_price"], errors="coerce")
    return (
        quotes.dropna(subset=["trade_date", "close_price"])
        .loc[lambda frame: frame["close_price"] > 0]
        .sort_values("trade_date")
        .drop_duplicates("trade_date", keep="last")
        .reset_index(drop=True)
    )


def build_events(spread: pd.DataFrame, quotes: pd.DataFrame) -> pd.DataFrame:
    if spread.empty or quotes.empty:
        return pd.DataFrame(columns=["signal_date", "entry_date", "exit_date", "holding_return"])

    signals = spread.loc[spread["is_signal"], ["signal_date", "spread_10y_3y", "rolling_pct_20d"]].copy()
    trade_dates = quotes["trade_date"].to_numpy()
    close_values = quotes["close_price"].to_numpy(dtype=float)

    rows: list[dict[str, object]] = []
    for _, signal in signals.iterrows():
        entry_idx = trade_dates.searchsorted(signal["signal_date"].to_datetime64(), side="right")
        # The entry date is counted as the first holding day.
        exit_idx = entry_idx + HOLDING_DAYS - 1
        if entry_idx >= len(trade_dates) or exit_idx >= len(trade_dates):
            continue

        entry_price = float(close_values[entry_idx])
        exit_price = float(close_values[exit_idx])
        rows.append(
            {
                "signal_date": signal["signal_date"],
                "spread_10y_3y": signal["spread_10y_3y"],
                "rolling_pct_20d": signal["rolling_pct_20d"],
                "entry_date": quotes.loc[entry_idx, "trade_date"],
                "exit_date": quotes.loc[exit_idx, "trade_date"],
                "entry_price": entry_price,
                "exit_price": exit_price,
                "holding_return": exit_price / entry_price - 1.0,
            }
        )
    return pd.DataFrame(rows)


def summarize(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(
            [{"样本数": 0, "平均收益(%)": "", "中位数收益(%)": "", "胜率(%)": ""}],
            columns=RESULT_COLUMNS,
        )

    row = {
        "样本数": int(len(events)),
        "平均收益(%)": fmt_pct(events["holding_return"].mean()),
        "中位数收益(%)": fmt_pct(events["holding_return"].median()),
        "胜率(%)": fmt_pct((events["holding_return"] > 0).mean()),
    }
    return pd.DataFrame([row], columns=RESULT_COLUMNS)


def build_result(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    spread = prepare_spread(raw)
    quotes = prepare_index_quotes(raw)
    events = build_events(spread, quotes)
    result = summarize(events)
    return result, events, spread


def main() -> pd.DataFrame:
    base_dir = Path(__file__).resolve().parent
    result, _, _ = build_result(load_raw_data())
    result.to_csv(base_dir / "result.csv", index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
