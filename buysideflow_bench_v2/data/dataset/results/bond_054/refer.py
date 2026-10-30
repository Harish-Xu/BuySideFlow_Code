#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe, shared_connection_session


BACKTEST_START = "2024-01-01"
BACKTEST_END_EXCLUSIVE = "2025-01-01"
CALENDAR_START = "2023-12-01"

MIN_SIGNAL_PRICE = 50.0
MAX_SIGNAL_PRICE = 110.0
MAX_SIGNAL_PREMIUM_PCT = 20.0
REGULAR_CONVERTIBLE_NATURE = 1

OUTPUT_COLUMNS = ["全年收益率", "最大回撤", "月度胜率"]


def sql_num_list(values: Iterable[int]) -> str:
    return ",".join(str(int(value)) for value in values)


def chunks(values: Iterable[int], size: int = 500):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start : start + size]


def fetch_trading_calendar() -> pd.DataFrame:
    sql = f"""
    SELECT DATE(TRADINGDATE) AS trade_date
    FROM qt_tradingdaynew FORCE INDEX (IX_QT_TRADINGDAYNEW)
    WHERE TRADINGDATE >= '{CALENDAR_START}'
      AND TRADINGDATE < '{BACKTEST_END_EXCLUSIVE}'
      AND SECUMARKET IN (83, 90)
      AND IFTRADINGDAY = 1
    GROUP BY DATE(TRADINGDATE)
    ORDER BY trade_date;
    """
    calendar = query_to_dataframe(sql, max_rows=0, timeout=120)
    if calendar.empty:
        return calendar
    calendar["trade_date"] = pd.to_datetime(calendar["trade_date"], errors="coerce")
    return calendar.dropna(subset=["trade_date"]).drop_duplicates("trade_date").sort_values("trade_date")


def build_month_schedule(calendar: pd.DataFrame) -> pd.DataFrame:
    if calendar.empty:
        return pd.DataFrame()

    days = calendar.copy()
    days["month"] = days["trade_date"].dt.to_period("M")
    month_bounds = days.groupby("month")["trade_date"].agg(["min", "max"])

    rows: list[dict] = []
    for month in pd.period_range("2024-01", "2024-12", freq="M"):
        previous_month = month - 1
        if month not in month_bounds.index or previous_month not in month_bounds.index:
            raise ValueError(f"缺少 {month} 或其上月的交易日历")
        rows.append(
            {
                "month": str(month),
                "signal_date": month_bounds.loc[previous_month, "max"],
                "entry_date": month_bounds.loc[month, "min"],
                "month_end_date": month_bounds.loc[month, "max"],
            }
        )
    return pd.DataFrame(rows)


def normalize_quotes(quotes: pd.DataFrame) -> pd.DataFrame:
    if quotes.empty:
        return quotes
    quotes["INNERCODE"] = pd.to_numeric(quotes["INNERCODE"], errors="coerce").astype("Int64")
    quotes["ID"] = pd.to_numeric(quotes["ID"], errors="coerce")
    quotes["trade_date"] = pd.to_datetime(quotes["trade_date"], errors="coerce")
    quotes["update_time"] = pd.to_datetime(quotes["update_time"], errors="coerce")
    for column in ["close_price", "conversion_price", "stock_price"]:
        if column in quotes.columns:
            quotes[column] = pd.to_numeric(quotes[column], errors="coerce")
    return (
        quotes.dropna(subset=["INNERCODE", "trade_date", "close_price"])
        .sort_values(["INNERCODE", "trade_date", "update_time", "ID"])
        .drop_duplicates(["INNERCODE", "trade_date"], keep="last")
        .reset_index(drop=True)
    )


def fetch_signal_quotes(signal_dates: Iterable[pd.Timestamp]) -> pd.DataFrame:
    date_list = ",".join(
        f"'{pd.Timestamp(date).date().isoformat()}'" for date in sorted(set(signal_dates))
    )
    sql = f"""
    SELECT
        q.ID,
        q.INNERCODE,
        DATE(q.TRADINGDAY) AS trade_date,
        q.CLOSEPRICE AS close_price,
        (SELECT cp.CONVERTPRICE FROM bond_conbdconvertprice cp
         WHERE cp.INNERCODE = q.INNERCODE AND cp.VALIDDATE <= q.TRADINGDAY
           AND cp.INFOPUBLDATE < DATE_ADD(DATE(q.TRADINGDAY), INTERVAL 1 DAY)
         ORDER BY cp.VALIDDATE DESC, cp.INFOPUBLDATE DESC, cp.ID DESC LIMIT 1) AS conversion_price,
        q.STOCKPRICE AS stock_price,
        q.UPDATETIME AS update_time
    FROM bond_conbdexchangequote q
    WHERE q.TRADINGDAY IN ({date_list})
      AND q.BONDNATURE = {REGULAR_CONVERTIBLE_NATURE}
      AND q.CLOSEPRICE IS NOT NULL;
    """
    quotes = query_to_dataframe(sql, max_rows=0, timeout=120)
    return normalize_quotes(quotes)


def fetch_holding_quotes(innercodes: Iterable[int]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    codes = sorted(set(int(value) for value in innercodes if pd.notna(value)))
    for part in chunks(codes):
        sql = f"""
        SELECT
            ID,
            INNERCODE,
            DATE(TRADINGDAY) AS trade_date,
            CLOSEPRICE AS close_price,
            UPDATETIME AS update_time
        FROM bond_conbdexchangequote FORCE INDEX (IX_BOND_CONBDEXCHANGEQUOTE)
        WHERE INNERCODE IN ({sql_num_list(part)})
          AND TRADINGDAY >= '{BACKTEST_START}'
          AND TRADINGDAY < '{BACKTEST_END_EXCLUSIVE}'
          AND BONDNATURE = {REGULAR_CONVERTIBLE_NATURE}
          AND CLOSEPRICE IS NOT NULL;
        """
        frame = query_to_dataframe(sql, max_rows=0, timeout=120)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return normalize_quotes(pd.concat(frames, ignore_index=True))


def signal_candidates(quotes: pd.DataFrame, signal_date: pd.Timestamp) -> pd.DataFrame:
    signal = quotes[quotes["trade_date"] == pd.Timestamp(signal_date)].copy()
    signal = signal[
        (signal["close_price"] > 0)
        & (signal["conversion_price"] > 0)
        & (signal["stock_price"] > 0)
    ].copy()
    if signal.empty:
        return signal

    signal["conversion_value"] = 100.0 * signal["stock_price"] / signal["conversion_price"]
    signal["signal_premium_pct"] = (
        signal["close_price"] / signal["conversion_value"] - 1.0
    ) * 100.0
    return signal[
        (signal["close_price"] > MIN_SIGNAL_PRICE)
        & (signal["close_price"] < MAX_SIGNAL_PRICE)
        & (signal["signal_premium_pct"] < MAX_SIGNAL_PREMIUM_PCT)
    ].copy()


def run_month(
    signal_quotes: pd.DataFrame,
    holding_quotes: pd.DataFrame,
    month: str,
    signal_date: pd.Timestamp,
    entry_date: pd.Timestamp,
    month_end_date: pd.Timestamp,
) -> tuple[dict, pd.DataFrame]:
    candidates = signal_candidates(signal_quotes, signal_date)
    if candidates.empty:
        return {
            "month": month,
            "signal_date": signal_date,
            "entry_date": entry_date,
            "month_end_date": month_end_date,
            "candidate_count": 0,
            "selected_count": 0,
            "monthly_return": 0.0,
        }, pd.DataFrame()

    entry = holding_quotes[
        (holding_quotes["trade_date"] == pd.Timestamp(entry_date))
        & (holding_quotes["INNERCODE"].isin(candidates["INNERCODE"]))
        & (holding_quotes["close_price"] > 0)
    ][["INNERCODE", "close_price"]].rename(columns={"close_price": "entry_price"})
    if entry.empty:
        return {
            "month": month,
            "signal_date": signal_date,
            "entry_date": entry_date,
            "month_end_date": month_end_date,
            "candidate_count": int(candidates["INNERCODE"].nunique()),
            "selected_count": 0,
            "monthly_return": 0.0,
        }, pd.DataFrame()

    selected_codes = entry["INNERCODE"].drop_duplicates()
    valuation = (
        holding_quotes[
            (holding_quotes["INNERCODE"].isin(selected_codes))
            & (holding_quotes["trade_date"] >= pd.Timestamp(entry_date))
            & (holding_quotes["trade_date"] <= pd.Timestamp(month_end_date))
            & (holding_quotes["close_price"] > 0)
        ]
        .sort_values(["INNERCODE", "trade_date", "update_time", "ID"])
        .drop_duplicates("INNERCODE", keep="last")
        [["INNERCODE", "trade_date", "close_price"]]
        .rename(columns={"trade_date": "valuation_date", "close_price": "valuation_price"})
    )
    detail = entry.merge(valuation, on="INNERCODE", how="inner")
    if detail.empty:
        raise ValueError(f"{month} 的建仓标的均缺少月末估值价格")

    detail["individual_return"] = detail["valuation_price"] / detail["entry_price"] - 1.0
    detail["month"] = month
    detail["signal_date"] = pd.Timestamp(signal_date)
    detail["entry_date"] = pd.Timestamp(entry_date)
    detail["month_end_date"] = pd.Timestamp(month_end_date)
    monthly_return = float(detail["individual_return"].mean())
    return {
        "month": month,
        "signal_date": signal_date,
        "entry_date": entry_date,
        "month_end_date": month_end_date,
        "candidate_count": int(candidates["INNERCODE"].nunique()),
        "selected_count": int(detail["INNERCODE"].nunique()),
        "monthly_return": monthly_return,
    }, detail


def build_backtest() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    calendar = fetch_trading_calendar()
    schedule = build_month_schedule(calendar)
    signal_quotes = fetch_signal_quotes(schedule["signal_date"])
    if signal_quotes.empty:
        raise ValueError("未取得12个信号日的可转债行情")

    candidate_codes: set[int] = set()
    for signal_date in schedule["signal_date"]:
        candidates = signal_candidates(signal_quotes, signal_date)
        candidate_codes.update(int(value) for value in candidates["INNERCODE"] if pd.notna(value))
    holding_quotes = fetch_holding_quotes(candidate_codes)
    if holding_quotes.empty and candidate_codes:
        raise ValueError("未取得候选转债的2024年持有期行情")

    monthly_rows: list[dict] = []
    detail_rows: list[pd.DataFrame] = []
    for row in schedule.itertuples(index=False):
        monthly, detail = run_month(
            signal_quotes,
            holding_quotes,
            row.month,
            row.signal_date,
            row.entry_date,
            row.month_end_date,
        )
        monthly_rows.append(monthly)
        if not detail.empty:
            detail_rows.append(detail)

    monthly = pd.DataFrame(monthly_rows).sort_values("month").reset_index(drop=True)
    if len(monthly) != 12:
        raise ValueError(f"月度结果应为12个月，实际为{len(monthly)}个月")

    monthly["portfolio_nav"] = (1.0 + monthly["monthly_return"]).cumprod()
    nav_path = pd.Series([1.0, *monthly["portfolio_nav"].tolist()], dtype="float64")
    drawdown = nav_path / nav_path.cummax() - 1.0
    annual_return = float(monthly["portfolio_nav"].iloc[-1] - 1.0)
    max_drawdown = float(drawdown.min())
    monthly_win_rate = float((monthly["monthly_return"] > 0).sum() / 12.0)

    result = pd.DataFrame(
        [
            {
                "全年收益率": round(annual_return * 100.0, 2),
                "最大回撤": round(max_drawdown * 100.0, 2),
                "月度胜率": round(monthly_win_rate * 100.0, 2),
            }
        ],
        columns=OUTPUT_COLUMNS,
    )
    detail = pd.concat(detail_rows, ignore_index=True) if detail_rows else pd.DataFrame()
    return result, monthly, detail


def main() -> pd.DataFrame:
    with shared_connection_session():
        result, _, _ = build_backtest()
    output_path = Path(__file__).with_name("result.csv")
    result.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_csv(index=False, float_format="%.2f").strip())
    return result


if __name__ == "__main__":
    main()
