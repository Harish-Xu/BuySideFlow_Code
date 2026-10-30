#!/usr/bin/env python3
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "sweagent" / "text2sql" / "db_connector.py").exists():
            return parent
    raise RuntimeError("Cannot locate repository root")


REPO_ROOT = _repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = pd.Timestamp("2025-06-30")
EVENT_START = pd.Timestamp("2025-01-01")
QUOTE_END = "2025-09-30"
HORIZON_DAYS = 20

OUTPUT_COLUMNS = [
    "转债代码",
    "转债简称",
    "首次满足日期",
    "20日转债收益",
    "20日正股收益",
    "20日转股溢价率变化",
    "20日最大回撤",
]


def _empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def _fetch_quote_history(
    innercodes: list[int],
    quote_start: pd.Timestamp,
    quote_end: pd.Timestamp,
) -> pd.DataFrame:
    if not innercodes:
        return pd.DataFrame()
    codes = sorted(set(int(x) for x in innercodes))
    frames: list[pd.DataFrame] = []
    for offset in range(0, len(codes), 60):
        code_sql = ",".join(str(x) for x in codes[offset : offset + 60])
        sql = f"""
        SELECT
            q.INNERCODE,
            q.TRADINGDAY,
            q.CLOSEPRICE,
            q.STOCKPRICE,
            q.NEWCONVETPRICE,
            q.CONVERTPREMIUMRATE,
            q.UPDATETIME,
            q.ID
        FROM bond_conbdexchangequote q FORCE INDEX (IX_BOND_CONBDEXCHANGEQUOTE)
        WHERE q.INNERCODE IN ({code_sql})
          AND q.TRADINGDAY BETWEEN '{quote_start:%Y-%m-%d}' AND '{quote_end:%Y-%m-%d}'
        ORDER BY q.INNERCODE, q.TRADINGDAY;
        """
        frame = query_to_dataframe(sql, max_rows=0, timeout=600)
        if frame is not None and not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    quote = pd.concat(frames, ignore_index=True)
    quote["INNERCODE"] = pd.to_numeric(quote["INNERCODE"]).astype("int64")
    quote["TRADINGDAY"] = pd.to_datetime(quote["TRADINGDAY"])
    quote["UPDATETIME"] = pd.to_datetime(quote["UPDATETIME"], errors="coerce")
    quote["ID"] = pd.to_numeric(quote["ID"], errors="coerce")
    for column in ["CLOSEPRICE", "STOCKPRICE", "NEWCONVETPRICE", "CONVERTPREMIUMRATE"]:
        quote[column] = pd.to_numeric(quote[column], errors="coerce")
    return quote


def _fetch_trade_calendar(history_start: pd.Timestamp) -> pd.DatetimeIndex:
    pre_sql = f"""
    SELECT trading_day
    FROM (
        SELECT DISTINCT TRADINGDAY AS trading_day
        FROM bond_conbdexchangequote
        WHERE TRADINGDAY >= '{history_start:%Y-%m-%d}'
          AND TRADINGDAY < '{EVENT_START:%Y-%m-%d}'
        ORDER BY trading_day DESC
    ) recent
    ORDER BY trading_day
    """
    signal_sql = f"""
    SELECT DISTINCT TRADINGDAY AS trading_day
    FROM bond_conbdexchangequote
    WHERE TRADINGDAY BETWEEN '{EVENT_START:%Y-%m-%d}' AND '{AS_OF_DATE:%Y-%m-%d}'
    ORDER BY trading_day
    """
    future_sql = f"""
    SELECT DISTINCT TRADINGDAY AS trading_day
    FROM bond_conbdexchangequote
    WHERE TRADINGDAY > '{AS_OF_DATE:%Y-%m-%d}'
      AND TRADINGDAY <= '{QUOTE_END}'
    ORDER BY trading_day
    LIMIT {HORIZON_DAYS}
    """
    pre_dates = query_to_dataframe(pre_sql, max_rows=0, timeout=600)
    signal_dates = query_to_dataframe(signal_sql, max_rows=0, timeout=600)
    future_dates = query_to_dataframe(future_sql, max_rows=0, timeout=600)
    if (
        pre_dates is None
        or signal_dates is None
        or signal_dates.empty
        or future_dates is None
        or len(future_dates) < HORIZON_DAYS
    ):
        return pd.DatetimeIndex([])
    dates = pd.concat([pre_dates, signal_dates, future_dates], ignore_index=True)
    return pd.DatetimeIndex(
        pd.to_datetime(dates["trading_day"], errors="coerce").dropna().unique()
    ).sort_values()


def _max_drawdown(prices: pd.Series) -> float:
    running_peak = prices.cummax()
    drawdown = prices / running_peak - 1.0
    return float(drawdown.min())


def _daily_call_terms(
    terms: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    convert_start: pd.Timestamp,
) -> pd.DataFrame:
    """Apply effective dates AND daily availability, retaining earlier versions.

    The task specifies dates, so each day's information cutoff is next midnight.
    Publication on that day is eligible; unknown publication is excluded.
    An incomplete or shortened revision supersedes its older version rather
    than recovering the old trigger parameters after the revised end date.
    """
    fields = ["call_trigger_ratio", "window_days", "required_hit_days"]
    daily = pd.DataFrame(float("nan"), index=range(len(calendar)), columns=fields)
    versions = terms.copy()
    for column in [
        "call_start_date", "call_end_date", "payment_year_start_date",
        "payment_year_end_date", "term_publish_time", "term_update_time",
    ]:
        versions[column] = pd.to_datetime(versions[column], errors="coerce")
    versions["available_time"] = versions["term_publish_time"]
    versions = versions.dropna(subset=["available_time", "call_start_date"])
    versions = versions.sort_values(
        ["available_time", "term_update_time", "term_id"], na_position="first"
    )
    versions["available_day"] = versions["available_time"].dt.normalize()
    # An end-date revision belongs to the same clause, not a separate version group.
    version_key = ["call_start_date", "payment_year_sequence"]
    versions["next_available_day"] = versions.groupby(version_key, dropna=False)[
        "available_day"
    ].shift(-1)
    versions = versions.sort_values(
        [
            "call_start_date", "payment_year_start_date", "payment_year_sequence",
            "available_time", "term_update_time", "term_id",
        ],
        na_position="first",
    )
    for _, term in versions.iterrows():
        effective_start = max(
            date for date in [
                convert_start, term["call_start_date"],
                term["payment_year_start_date"], term["available_day"],
            ] if not pd.isna(date)
        )
        end_dates = [
            date for date in [term["call_end_date"], term["payment_year_end_date"]]
            if not pd.isna(date)
        ]
        effective_end = min(end_dates) if end_dates else AS_OF_DATE
        mask = (calendar >= effective_start) & (calendar <= effective_end)
        if pd.notna(term["next_available_day"]):
            mask &= calendar < term["next_available_day"]
        for column in fields:
            daily.loc[mask, column] = pd.to_numeric(term[column], errors="coerce")
    return daily


def _build_event(
    terms: pd.DataFrame,
    quote: pd.DataFrame,
    trading_calendar: pd.DatetimeIndex,
) -> dict[str, object] | None:
    quote = (
        quote.sort_values(["TRADINGDAY", "UPDATETIME", "ID"], na_position="first")
        .drop_duplicates("TRADINGDAY", keep="last")
        .copy()
    )
    if quote.empty or trading_calendar.empty:
        return None

    terms = terms.copy()
    convert_start = pd.to_datetime(terms["convert_start_date"], errors="coerce").min()
    if pd.isna(convert_start):
        return None

    calendar = trading_calendar[trading_calendar >= convert_start]
    quote = (
        pd.DataFrame({"TRADINGDAY": calendar})
        .merge(quote, on="TRADINGDAY", how="left")
        .reset_index(drop=True)
    )

    daily_terms = _daily_call_terms(terms, calendar, convert_start)
    for column in daily_terms:
        quote[column] = daily_terms[column].to_numpy()
    condition_valid = (
        (quote["STOCKPRICE"] > 0)
        & (quote["NEWCONVETPRICE"] > 0)
        & (quote["call_trigger_ratio"] > 0)
    )
    quote["above_call_level"] = condition_valid & (
        quote["STOCKPRICE"]
        >= quote["NEWCONVETPRICE"] * quote["call_trigger_ratio"]
    )

    signal_pos: int | None = None
    hit_values = quote["above_call_level"].astype(int)
    for pos, row in quote.iterrows():
        if row["TRADINGDAY"] > AS_OF_DATE:
            break
        if (
            pd.isna(row["call_trigger_ratio"])
            or row["call_trigger_ratio"] <= 0
            or pd.isna(row["window_days"])
            or pd.isna(row["required_hit_days"])
        ):
            continue
        if row["window_days"] % 1 != 0 or row["required_hit_days"] % 1 != 0:
            continue
        window_days = int(row["window_days"])
        required_hit_days = int(row["required_hit_days"])
        if (
            window_days <= 0
            or required_hit_days <= 0
            or required_hit_days > window_days
            or pos + 1 < window_days
        ):
            continue
        if int(hit_values.iloc[pos - window_days + 1 : pos + 1].sum()) >= required_hit_days:
            signal_pos = int(pos)
            break

    if signal_pos is None:
        return None
    signal_day = quote.loc[signal_pos, "TRADINGDAY"]
    if signal_day < EVENT_START or signal_day > AS_OF_DATE:
        return None

    horizon = quote.iloc[signal_pos : signal_pos + HORIZON_DAYS + 1].copy()
    if len(horizon) != HORIZON_DAYS + 1:
        return None
    horizon_valid = (
        (horizon["CLOSEPRICE"] > 0)
        & (horizon["STOCKPRICE"] > 0)
        & (horizon["NEWCONVETPRICE"] > 0)
        & horizon["CONVERTPREMIUMRATE"].notna()
    )
    if not horizon_valid.all():
        return None

    entry = horizon.iloc[0]
    exit_row = horizon.iloc[-1]
    path = horizon["CLOSEPRICE"]
    return {
        "转债代码": str(terms.iloc[0]["SECUCODE"]),
        "转债简称": str(terms.iloc[0]["SECUABBR"]),
        "首次满足日期": entry["TRADINGDAY"].strftime("%Y-%m-%d"),
        "20日转债收益": (float(exit_row["CLOSEPRICE"]) / float(entry["CLOSEPRICE"]) - 1.0) * 100.0,
        "20日正股收益": (float(exit_row["STOCKPRICE"]) / float(entry["STOCKPRICE"]) - 1.0) * 100.0,
        "20日转股溢价率变化": float(exit_row["CONVERTPREMIUMRATE"]) - float(entry["CONVERTPREMIUMRATE"]),
        "20日最大回撤": _max_drawdown(path) * 100.0,
    }


def _build_result() -> pd.DataFrame:
    term_sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    terms = query_to_dataframe(term_sql, max_rows=0, timeout=600)
    if terms is None or terms.empty:
        return _empty_result()

    terms = terms.copy()
    terms["INNERCODE"] = pd.to_numeric(terms["INNERCODE"]).astype("int64")
    for column in [
        "call_trigger_ratio",
        "window_days",
        "required_hit_days",
        "payment_year_sequence",
        "term_id",
    ]:
        terms[column] = pd.to_numeric(terms[column], errors="coerce")
    for column in [
        "call_start_date",
        "call_end_date",
        "convert_start_date",
        "payment_year_start_date",
        "payment_year_end_date",
        "term_publish_time",
        "term_update_time",
    ]:
        terms[column] = pd.to_datetime(terms[column], errors="coerce")
    history_start = terms["convert_start_date"].min()
    trading_calendar = _fetch_trade_calendar(history_start)
    if trading_calendar.empty:
        return _empty_result()
    quote = _fetch_quote_history(
        terms["INNERCODE"].tolist(),
        trading_calendar.min(),
        trading_calendar.max(),
    )
    if quote.empty:
        return _empty_result()

    events: list[dict[str, object]] = []
    for innercode, group in quote.groupby("INNERCODE", sort=False):
        term_group = terms[terms["INNERCODE"] == innercode]
        if term_group.empty:
            continue
        event = _build_event(term_group, group, trading_calendar)
        if event is not None:
            events.append(event)

    if not events:
        return _empty_result()

    result = pd.DataFrame(events)
    result = result.sort_values(
        ["20日最大回撤", "转债代码"],
        ascending=[True, True],
    ).reset_index(drop=True)
    return result[OUTPUT_COLUMNS]


def main() -> pd.DataFrame:
    started = time.time()
    result = _build_result()
    result_dir = Path(__file__).resolve().parent
    result.to_csv(result_dir / "result.csv", index=False, encoding="utf-8-sig", float_format="%.2f")
    print(f"# elapsed_seconds={time.time() - started:.2f}", file=sys.stderr)
    return result


if __name__ == "__main__":
    print(main().to_csv(index=False, float_format="%.2f"), end="")
