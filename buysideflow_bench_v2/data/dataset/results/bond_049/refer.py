#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = pd.Timestamp("2025-01-03")
SIGNAL_START = pd.Timestamp("2024-01-01")
SIGNAL_END = pd.Timestamp("2024-12-31")
ROLLING_LOOKBACK = pd.DateOffset(years=1)
PERCENTILE_LEVEL = 0.95
SHORT_INDEX_CODE = "CBA00621"  # 中债-国债总指数_1-3年
LONG_INDEX_CODE = "CBA00651"  # 中债-国债总指数_7-10年
HOLDING_DAYS_LIST = [5, 20]
RESULT_COLUMNS = [
    "持有期(指数交易日)",
    "样本数",
    "1-3年平均收益(%)",
    "7-10年平均收益(%)",
    "短久期相对长久期平均收益差(%)",
    "短久期占优胜率(%)",
]


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


def prepare_funding_gap(raw: pd.DataFrame) -> pd.DataFrame:
    gap = (
        raw.loc[raw["series_name"].eq("funding_gap"), ["obs_date", "series_value"]]
        .dropna()
        .rename(columns={"obs_date": "signal_date", "series_value": "funding_gap"})
        .copy()
    )
    gap["signal_date"] = pd.to_datetime(gap["signal_date"], errors="coerce")
    gap["funding_gap"] = pd.to_numeric(gap["funding_gap"], errors="coerce")
    gap = (
        gap.dropna(subset=["signal_date", "funding_gap"])
        .sort_values("signal_date")
        .drop_duplicates("signal_date", keep="last")
        .reset_index(drop=True)
    )
    if gap.empty:
        return gap

    thresholds: list[float] = []
    for row in gap.itertuples(index=False):
        start = row.signal_date - ROLLING_LOOKBACK
        window = gap.loc[
            (gap["signal_date"] >= start) & (gap["signal_date"] <= row.signal_date),
            "funding_gap",
        ]
        thresholds.append(float(window.quantile(PERCENTILE_LEVEL)) if not window.empty else float("nan"))

    gap["rolling_1y_p95"] = thresholds
    gap["raw_trigger"] = (
        gap["signal_date"].between(SIGNAL_START, SIGNAL_END)
        & gap["rolling_1y_p95"].notna()
        & (gap["funding_gap"] >= gap["rolling_1y_p95"])
    )
    # Consecutive funding-rate observation triggers are one episode; keep the first day.
    gap["is_signal"] = gap["raw_trigger"] & ~gap["raw_trigger"].shift(fill_value=False)
    return gap


def prepare_index_quotes(raw: pd.DataFrame) -> pd.DataFrame:
    quotes = (
        raw.loc[raw["series_name"].eq("index_price"), ["asset_id", "obs_date", "series_value"]]
        .dropna()
        .rename(columns={"asset_id": "index_code", "obs_date": "trade_date", "series_value": "close_price"})
        .copy()
    )
    quotes["trade_date"] = pd.to_datetime(quotes["trade_date"], errors="coerce")
    quotes["close_price"] = pd.to_numeric(quotes["close_price"], errors="coerce")
    quotes = (
        quotes.dropna(subset=["index_code", "trade_date", "close_price"])
        .loc[lambda frame: frame["index_code"].isin([SHORT_INDEX_CODE, LONG_INDEX_CODE])]
        .loc[lambda frame: frame["close_price"] > 0]
        .sort_values(["index_code", "trade_date"])
        .drop_duplicates(["index_code", "trade_date"], keep="last")
    )
    return (
        quotes.pivot_table(index="trade_date", columns="index_code", values="close_price", aggfunc="last")
        .dropna(subset=[SHORT_INDEX_CODE, LONG_INDEX_CODE])
        .sort_index()
    )


def build_events(gap: pd.DataFrame, index_prices: pd.DataFrame) -> pd.DataFrame:
    if gap.empty or index_prices.empty:
        return pd.DataFrame()

    signals = gap.loc[gap["is_signal"], ["signal_date", "funding_gap", "rolling_1y_p95"]].copy()
    trade_dates = index_prices.index.to_numpy()
    rows: list[dict[str, object]] = []
    for _, signal in signals.iterrows():
        entry_idx = trade_dates.searchsorted(signal["signal_date"].to_datetime64(), side="right")
        if entry_idx >= len(trade_dates):
            continue

        entry_date = index_prices.index[entry_idx]
        entry_short = float(index_prices.iloc[entry_idx][SHORT_INDEX_CODE])
        entry_long = float(index_prices.iloc[entry_idx][LONG_INDEX_CODE])
        for holding_days in HOLDING_DAYS_LIST:
            # The entry date is counted as the first holding day.
            exit_idx = entry_idx + holding_days - 1
            if exit_idx >= len(index_prices):
                continue
            exit_date = index_prices.index[exit_idx]
            exit_short = float(index_prices.iloc[exit_idx][SHORT_INDEX_CODE])
            exit_long = float(index_prices.iloc[exit_idx][LONG_INDEX_CODE])
            short_ret = exit_short / entry_short - 1.0
            long_ret = exit_long / entry_long - 1.0
            rows.append(
                {
                    "holding_days": holding_days,
                    "signal_date": signal["signal_date"],
                    "funding_gap": signal["funding_gap"],
                    "rolling_1y_p95": signal["rolling_1y_p95"],
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "entry_short": entry_short,
                    "exit_short": exit_short,
                    "entry_long": entry_long,
                    "exit_long": exit_long,
                    "short_return": short_ret,
                    "long_return": long_ret,
                    "relative_return": short_ret - long_ret,
                    "short_outperform": short_ret > long_ret,
                }
            )
    return pd.DataFrame(rows)


def summarize(events: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for holding_days in HOLDING_DAYS_LIST:
        group = events.loc[events["holding_days"].eq(holding_days)] if not events.empty else pd.DataFrame()
        rows.append(
            {
                "持有期(指数交易日)": holding_days,
                "样本数": int(len(group)),
                "1-3年平均收益(%)": "" if group.empty else fmt_pct(group["short_return"].mean()),
                "7-10年平均收益(%)": "" if group.empty else fmt_pct(group["long_return"].mean()),
                "短久期相对长久期平均收益差(%)": "" if group.empty else fmt_pct(group["relative_return"].mean()),
                "短久期占优胜率(%)": "" if group.empty else fmt_pct(group["short_outperform"].mean()),
            }
        )
    return pd.DataFrame(rows, columns=RESULT_COLUMNS)


def build_result(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    gap = prepare_funding_gap(raw)
    index_prices = prepare_index_quotes(raw)
    events = build_events(gap, index_prices)
    return summarize(events), events, gap


def main() -> pd.DataFrame:
    base_dir = Path(__file__).resolve().parent
    result, _, _ = build_result(load_raw_data())
    result.to_csv(base_dir / "result.csv", index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
