#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe, shared_connection_session


DISCLOSURE_START = pd.Timestamp("2025-01-01")
OBSERVE_DATE = pd.Timestamp("2026-01-12")
MIN_REPORT_PERIOD = pd.Timestamp("2024-12-31")
MARKET_CODES = (83, 90)
BOARD_CODES = (1,)
PERIODIC_REPORT_SOURCE_CODES = (110101, 110102, 110103, 110104)
PERIODIC_REPORT_BULLETIN_TYPE = 20
DEFAULT_CHUNK_SIZE = 700
PRICE_PAIR_CHUNK_SIZE = 800
RESULT_COLUMNS = ["有效报告期数", "多空收益均值(%)", "多空收益标准差(%)"]


def sql_date(value: pd.Timestamp) -> str:
    return value.date().isoformat()


def int_list(values: Iterable[int]) -> str:
    cleaned = sorted({int(v) for v in values if pd.notna(v)})
    if not cleaned:
        raise ValueError("empty integer list for SQL IN clause")
    return ",".join(str(v) for v in cleaned)


def chunks(values: Iterable[int], size: int = DEFAULT_CHUNK_SIZE) -> Iterable[list[int]]:
    vals = sorted({int(v) for v in values if pd.notna(v)})
    for i in range(0, len(vals), size):
        yield vals[i : i + size]


def query(sql: str, *, timeout: int = 600) -> pd.DataFrame:
    df = query_to_dataframe(sql, max_rows=0, timeout=timeout)
    if df is None:
        raise ValueError("SQL execution returned None")
    return df


def log(message: str) -> None:
    print(f"[stock_078] {message}", flush=True)


def normalize_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.normalize()


def fetch_stock_base() -> pd.DataFrame:
    sql = f"""
    SELECT COMPANYCODE, INNERCODE, SECUCODE, LISTEDSECTOR
    FROM (
        SELECT
            s.COMPANYCODE,
            s.INNERCODE,
            s.SECUCODE,
            s.LISTEDSECTOR,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY
                    s.SECUMARKET,
                    s.SECUCODE,
                    s.INNERCODE
            ) AS rn
        FROM secumain s
        WHERE s.SECUMARKET IN ({int_list(MARKET_CODES)})
          AND s.SECUCATEGORY = 1
          AND s.LISTEDSECTOR IN ({",".join(map(str, BOARD_CODES))})
          AND s.COMPANYCODE IS NOT NULL
          AND s.INNERCODE IS NOT NULL
          AND s.SECUCODE IS NOT NULL
    ) x
    WHERE rn = 1
    """
    df = query(sql, timeout=120)
    if df.empty:
        raise ValueError("stock_base is empty")
    for col in ["COMPANYCODE", "INNERCODE", "LISTEDSECTOR"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    df["SECUCODE"] = df["SECUCODE"].astype(str)
    return df.dropna(subset=["COMPANYCODE", "INNERCODE"])


def fetch_periodic_report_sources(stock_base: pd.DataFrame) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    company_chunks = list(chunks(stock_base["COMPANYCODE"]))
    for idx, company_codes in enumerate(company_chunks, start=1):
        log(f"fetch lc_maindatanew source chunk {idx}/{len(company_chunks)}")
        sql = f"""
        SELECT
            ID,
            JSID,
            COMPANYCODE,
            ENDDATE,
            INFOPUBLDATE,
            BULLETINTYPE,
            INFOSOURCECODE,
            INFOSOURCE,
            INSERTTIME
        FROM lc_maindatanew
        WHERE COMPANYCODE IN ({int_list(company_codes)})
          AND ENDDATE >= '{sql_date(MIN_REPORT_PERIOD)}'
          AND ENDDATE < DATE_ADD('{sql_date(OBSERVE_DATE)}', INTERVAL 1 DAY)
          AND INFOPUBLDATE >= '{sql_date(DISCLOSURE_START)}'
          AND INFOPUBLDATE < DATE_ADD('{sql_date(OBSERVE_DATE)}', INTERVAL 1 DAY)
          AND CAST(BULLETINTYPE AS UNSIGNED) = {PERIODIC_REPORT_BULLETIN_TYPE}
          AND INFOSOURCECODE IN ({int_list(PERIODIC_REPORT_SOURCE_CODES)})
          AND INFOPUBLDATE IS NOT NULL
          AND ENDDATE IS NOT NULL
        """
        part = query(sql, timeout=240)
        if not part.empty:
            parts.append(part)
    if not parts:
        raise ValueError("No periodic-report source rows for target universe")

    sources = pd.concat(parts, ignore_index=True)
    sources["COMPANYCODE"] = pd.to_numeric(sources["COMPANYCODE"], errors="coerce").astype("Int64")
    sources["report_period"] = normalize_date(sources["ENDDATE"])
    sources["publish_date"] = normalize_date(sources["INFOPUBLDATE"])
    sources["source_code"] = pd.to_numeric(sources["INFOSOURCECODE"], errors="coerce").astype("Int64")
    sources["bulletin_type"] = pd.to_numeric(sources["BULLETINTYPE"], errors="coerce").astype("Int64")
    sources["source_sort_time"] = pd.to_datetime(
        sources["INSERTTIME"].combine_first(sources["INFOPUBLDATE"]), errors="coerce"
    )
    sources["ID"] = pd.to_numeric(sources["ID"], errors="coerce")
    sources["JSID"] = pd.to_numeric(sources["JSID"], errors="coerce")

    quarter_end = sources["report_period"].dt.strftime("%m-%d").isin(["03-31", "06-30", "09-30", "12-31"])
    sources = sources.loc[
        quarter_end
        & sources["COMPANYCODE"].notna()
        & sources["report_period"].notna()
        & sources["publish_date"].notna()
        & sources["source_code"].isin(PERIODIC_REPORT_SOURCE_CODES)
        & (sources["bulletin_type"] == PERIODIC_REPORT_BULLETIN_TYPE)
    ].copy()
    if sources.empty:
        raise ValueError("No valid first periodic-report source rows after source filtering")

    sources = sources.sort_values(
        ["COMPANYCODE", "report_period", "publish_date", "source_sort_time", "ID", "JSID"],
        ascending=True,
        na_position="last",
    )
    sources = sources.drop_duplicates(["COMPANYCODE", "report_period"], keep="first")
    return sources[
        [
            "COMPANYCODE",
            "report_period",
            "publish_date",
            "source_code",
            "INFOSOURCE",
            "bulletin_type",
        ]
    ].rename(columns={"INFOSOURCE": "source_name"})


def fetch_financial_events(stock_base: pd.DataFrame) -> pd.DataFrame:
    periodic_sources = fetch_periodic_report_sources(stock_base)
    parts: list[pd.DataFrame] = []
    company_chunks = list(chunks(stock_base["COMPANYCODE"]))
    for idx, company_codes in enumerate(company_chunks, start=1):
        log(f"fetch lc_mainindexnew chunk {idx}/{len(company_chunks)}")
        sql = f"""
        SELECT
            ID,
            JSID,
            COMPANYCODE,
            ENDDATE,
            INFOPUBLDATE,
            OPERATINGREVENUEGROWRATE,
            INSERTTIME
        FROM lc_mainindexnew
        WHERE COMPANYCODE IN ({int_list(company_codes)})
          AND ENDDATE >= '{sql_date(MIN_REPORT_PERIOD)}'
          AND ENDDATE < DATE_ADD('{sql_date(OBSERVE_DATE)}', INTERVAL 1 DAY)
          AND INFOPUBLDATE < DATE_ADD('{sql_date(OBSERVE_DATE)}', INTERVAL 1 DAY)
          AND OPERATINGREVENUEGROWRATE IS NOT NULL
          AND INFOPUBLDATE IS NOT NULL
          AND ENDDATE IS NOT NULL
        """
        part = query(sql, timeout=240)
        if not part.empty:
            parts.append(part)
    if not parts:
        raise ValueError("No lc_mainindexnew rows for target universe")

    fin = pd.concat(parts, ignore_index=True)
    fin["COMPANYCODE"] = pd.to_numeric(fin["COMPANYCODE"], errors="coerce").astype("Int64")
    fin["report_period"] = normalize_date(fin["ENDDATE"])
    fin["metric_publish_date"] = normalize_date(fin["INFOPUBLDATE"])
    fin["revenue_growth_rate"] = pd.to_numeric(fin["OPERATINGREVENUEGROWRATE"], errors="coerce")
    fin["sort_update_time"] = pd.to_datetime(
        fin["INSERTTIME"].combine_first(fin["INFOPUBLDATE"]), errors="coerce"
    )
    fin["ID"] = pd.to_numeric(fin["ID"], errors="coerce")
    fin["JSID"] = pd.to_numeric(fin["JSID"], errors="coerce")

    quarter_end = fin["report_period"].dt.strftime("%m-%d").isin(["03-31", "06-30", "09-30", "12-31"])
    fin = fin.loc[
        quarter_end
        & fin["report_period"].notna()
        & fin["metric_publish_date"].notna()
        & fin["revenue_growth_rate"].notna()
    ].copy()
    fin = fin.merge(periodic_sources, on=["COMPANYCODE", "report_period"], how="inner")
    # Use the metric attached to the first periodic disclosure, not an earlier
    # earnings flash or a later corrected/non-missing value.
    fin = fin[fin['metric_publish_date'] == fin['publish_date']].copy()
    fin = fin.sort_values(
        ["COMPANYCODE", "report_period", "metric_publish_date", "sort_update_time", "ID", "JSID"],
        ascending=True,
        na_position="last",
    )
    first = fin.drop_duplicates(["COMPANYCODE", "report_period"], keep="first")
    first = first.loc[
        (first["publish_date"] >= DISCLOSURE_START)
        & (first["publish_date"] <= OBSERVE_DATE)
    ].copy()
    first = first.merge(stock_base, on="COMPANYCODE", how="inner")
    if first.empty:
        raise ValueError("No first-disclosure financial events in the requested window")
    return first[
        [
            "COMPANYCODE",
            "INNERCODE",
            "SECUCODE",
            "LISTEDSECTOR",
            "report_period",
            "publish_date",
            "revenue_growth_rate",
            "source_code",
            "source_name",
            "bulletin_type",
        ]
    ].copy()


def fetch_trading_calendar() -> pd.Series:
    sql = f"""
    SELECT DISTINCT TRADINGDATE AS trading_day
    FROM qt_tradingdaynew
    WHERE TRADINGDATE >= '{sql_date(DISCLOSURE_START)}'
      AND TRADINGDATE < DATE_ADD('{sql_date(OBSERVE_DATE)}', INTERVAL 1 DAY)
      AND IFTRADINGDAY = 1
      AND SECUMARKET IN ({int_list(MARKET_CODES)})
    ORDER BY TRADINGDATE
    """
    cal = query(sql, timeout=120)
    if cal.empty:
        raise ValueError("A-share trading calendar is empty")
    return normalize_date(cal["trading_day"]).dropna().drop_duplicates().sort_values().reset_index(drop=True)


def add_tracking_dates(events: pd.DataFrame, trading_days: pd.Series) -> pd.DataFrame:
    values = trading_days.to_numpy(dtype="datetime64[ns]")
    publish_values = events["publish_date"].to_numpy(dtype="datetime64[ns]")
    base_pos = np.searchsorted(values, publish_values, side="right")
    future_pos = base_pos + 20
    valid = future_pos < len(values)
    out = events.loc[valid].copy()
    out["base_date"] = pd.to_datetime(values[base_pos[valid]])
    out["future_date"] = pd.to_datetime(values[future_pos[valid]])
    return out


def fetch_prices(events: pd.DataFrame) -> pd.DataFrame:
    base_pairs = events[["INNERCODE", "base_date"]].rename(columns={"base_date": "trading_day"})
    future_pairs = events[["INNERCODE", "future_date"]].rename(columns={"future_date": "trading_day"})
    requested_pairs = pd.concat([base_pairs, future_pairs], ignore_index=True)
    requested_pairs["INNERCODE"] = pd.to_numeric(requested_pairs["INNERCODE"], errors="coerce").astype("Int64")
    requested_pairs["trading_day"] = normalize_date(requested_pairs["trading_day"])
    requested_pairs = requested_pairs.dropna(subset=["INNERCODE", "trading_day"]).drop_duplicates()
    if requested_pairs.empty:
        raise ValueError("No price lookup pairs for event windows")

    parts: list[pd.DataFrame] = []
    pair_rows = list(requested_pairs.itertuples(index=False))
    pair_chunks = [pair_rows[i : i + PRICE_PAIR_CHUNK_SIZE] for i in range(0, len(pair_rows), PRICE_PAIR_CHUNK_SIZE)]
    for idx, pair_chunk in enumerate(pair_chunks, start=1):
        log(f"fetch qt_performancedata pair chunk {idx}/{len(pair_chunks)}")
        pair_values = ",".join(
            f"('{sql_date(pd.Timestamp(row.trading_day))}',{int(row.INNERCODE)})"
            for row in pair_chunk
        )
        sql = f"""
        SELECT
            INNERCODE,
            TRADINGDAY,
            BACKWARDPRICE,
            UPDATETIME,
            ID,
            JSID
        FROM qt_performancedata
        WHERE (TRADINGDAY, INNERCODE) IN ({pair_values})
          AND BACKWARDPRICE > 0
        """
        part = query(sql, timeout=240)
        if not part.empty:
            parts.append(part)
    if not parts:
        raise ValueError("No backward-adjusted prices for event windows")
    px = pd.concat(parts, ignore_index=True)
    px["INNERCODE"] = pd.to_numeric(px["INNERCODE"], errors="coerce").astype("Int64")
    px["trading_day"] = normalize_date(px["TRADINGDAY"])
    px["BACKWARDPRICE"] = pd.to_numeric(px["BACKWARDPRICE"], errors="coerce")
    px["UPDATETIME"] = pd.to_datetime(px["UPDATETIME"], errors="coerce")
    px["ID"] = pd.to_numeric(px["ID"], errors="coerce")
    px["JSID"] = pd.to_numeric(px["JSID"], errors="coerce")
    px = px.dropna(subset=["INNERCODE", "trading_day", "BACKWARDPRICE"])
    px = px.sort_values(["INNERCODE", "trading_day", "UPDATETIME", "ID", "JSID"], na_position="last")
    return px.drop_duplicates(["INNERCODE", "trading_day"], keep="last")


def attach_returns(events: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    base_px = prices[["INNERCODE", "trading_day", "BACKWARDPRICE"]].rename(
        columns={"trading_day": "base_date", "BACKWARDPRICE": "base_backward_price"}
    )
    future_px = prices[["INNERCODE", "trading_day", "BACKWARDPRICE"]].rename(
        columns={"trading_day": "future_date", "BACKWARDPRICE": "future_backward_price"}
    )
    out = events.merge(base_px, on=["INNERCODE", "base_date"], how="inner")
    out = out.merge(future_px, on=["INNERCODE", "future_date"], how="inner")
    out = out.loc[(out["base_backward_price"] > 0) & (out["future_backward_price"] > 0)].copy()
    out["return_20d"] = out["future_backward_price"] / out["base_backward_price"] - 1
    return out


def fetch_list_status(innercodes: Iterable[int]) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for code_chunk in chunks(innercodes):
        sql = f"""
        SELECT INNERCODE, SECUMARKET, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID
        FROM lc_liststatus
        WHERE INNERCODE IN ({int_list(code_chunk)})
          AND SECUMARKET IN ({int_list(MARKET_CODES)})
          AND CHANGETYPE BETWEEN 1 AND 9
          AND CHANGEDATE < DATE_ADD('{sql_date(OBSERVE_DATE)}', INTERVAL 1 DAY)
          AND (INSERTTIME IS NULL OR INSERTTIME < DATE_ADD('{sql_date(OBSERVE_DATE)}', INTERVAL 1 DAY))
        """
        part = query(sql, timeout=180)
        if not part.empty:
            parts.append(part)
    if not parts:
        return pd.DataFrame(columns=["INNERCODE", "CHANGEDATE", "CHANGETYPE", "INFO_TIME", "JSID", "ID"])
    status = pd.concat(parts, ignore_index=True)
    status["INNERCODE"] = pd.to_numeric(status["INNERCODE"], errors="coerce").astype("Int64")
    status["CHANGEDATE"] = pd.to_datetime(status["CHANGEDATE"], errors="coerce")
    status["INSERTTIME"] = pd.to_datetime(status["INSERTTIME"], errors="coerce")
    status["INFO_TIME"] = status["INSERTTIME"].fillna(status["CHANGEDATE"])
    status["CHANGETYPE"] = pd.to_numeric(status["CHANGETYPE"], errors="coerce")
    status["JSID"] = pd.to_numeric(status["JSID"], errors="coerce")
    status["ID"] = pd.to_numeric(status["ID"], errors="coerce")
    return status.dropna(subset=["INNERCODE", "CHANGEDATE", "CHANGETYPE"])


def listed_at_base(row: pd.Series, status_by_code: dict[int, pd.DataFrame]) -> bool:
    recs = status_by_code.get(int(row["INNERCODE"]))
    if recs is None or recs.empty:
        return False
    cutoff = row["base_date"] + pd.Timedelta(days=1)
    visible = recs.loc[(recs["CHANGEDATE"] < cutoff) & (recs["INFO_TIME"] < cutoff)]
    if visible.empty:
        return False
    latest = visible.sort_values(["CHANGEDATE", "INFO_TIME", "JSID", "ID"], na_position="last").iloc[-1]
    return int(latest["CHANGETYPE"]) in (1, 3)


def fetch_st_records(innercodes: Iterable[int]) -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    st_columns = [
        "INNERCODE",
        "RISKALERTTYPE",
        "IMPLANNOUCEDATE",
        "IMPLEMENTDATE",
        "REMOVEINFOPUBLDATE",
        "REMOVEDATE",
        "INSERTTIME",
        "JSID",
    ]
    for code_chunk in chunks(innercodes):
        sql = f"""
        SELECT
            INNERCODE,
            RISKALERTTYPE,
            IMPLANNOUCEDATE,
            IMPLEMENTDATE,
            REMOVEINFOPUBLDATE,
            REMOVEDATE,
            INSERTTIME,
            JSID
        FROM cs_riskalert
        WHERE INNERCODE IN ({int_list(code_chunk)})
          AND RISKALERTTYPE IN (1, 5, 12, 14)
          AND IMPLEMENTDATE < DATE_ADD('{sql_date(OBSERVE_DATE)}', INTERVAL 1 DAY)
        """
        part = query(sql, timeout=180)
        if not part.empty:
            parts.append(part)
    parts = [part for part in parts if not part.empty and not part.dropna(how="all").empty]
    if not parts:
        return pd.DataFrame(
            columns=["INNERCODE", "ANNOUNCE_DATE", "IMPLEMENTDATE", "REMOVEINFOPUBLDATE", "REMOVEDATE"]
        )
    st = pd.concat([part.dropna(axis=1, how="all") for part in parts], ignore_index=True).reindex(
        columns=st_columns
    )
    st["INNERCODE"] = pd.to_numeric(st["INNERCODE"], errors="coerce").astype("Int64")
    for col in ["IMPLANNOUCEDATE", "IMPLEMENTDATE", "REMOVEINFOPUBLDATE", "REMOVEDATE"]:
        st[col] = pd.to_datetime(st[col], errors="coerce")
    st["ANNOUNCE_DATE"] = st["IMPLANNOUCEDATE"].combine_first(st["IMPLEMENTDATE"])
    st["INFO_TIME"] = st["ANNOUNCE_DATE"]
    return st.dropna(subset=["INNERCODE", "IMPLEMENTDATE", "ANNOUNCE_DATE"])


def st_active_at_base(row: pd.Series, st_by_code: dict[int, pd.DataFrame]) -> bool:
    recs = st_by_code.get(int(row["INNERCODE"]))
    if recs is None or recs.empty:
        return False
    cutoff = row["base_date"] + pd.Timedelta(days=1)
    active = recs.loc[
        (recs["INFO_TIME"] < cutoff)
        & (recs["ANNOUNCE_DATE"] < cutoff)
        & (recs["IMPLEMENTDATE"] < cutoff)
        & (
            recs["REMOVEDATE"].isna()
            | (recs["REMOVEDATE"] >= cutoff)
            | (recs["REMOVEINFOPUBLDATE"].fillna(recs["REMOVEDATE"]) >= cutoff)
        )
    ]
    return not active.empty


def apply_status_filters(events: pd.DataFrame) -> pd.DataFrame:
    status = fetch_list_status(events["INNERCODE"])
    status_by_code = {int(code): frame.copy() for code, frame in status.groupby("INNERCODE")}
    st = fetch_st_records(events["INNERCODE"])
    st_by_code = {int(code): frame.copy() for code, frame in st.groupby("INNERCODE")}

    listed_mask = events.apply(listed_at_base, axis=1, status_by_code=status_by_code)
    st_mask = events.apply(st_active_at_base, axis=1, st_by_code=st_by_code)
    out = events.loc[listed_mask & ~st_mask].copy()
    if out.empty:
        raise ValueError("No events left after listing-status and ST filters")
    return out


def assign_ntile(frame: pd.DataFrame, n_tiles: int = 5) -> pd.DataFrame:
    ordered = frame.sort_values(["revenue_growth_rate", "SECUCODE", "INNERCODE"]).reset_index(drop=True)
    n = len(ordered)
    if n < n_tiles:
        ordered["quintile_group"] = np.nan
        return ordered
    ordered["row_number"] = np.arange(1, n + 1)
    base_size, extra = divmod(n, n_tiles)
    sizes = [base_size + (i < extra) for i in range(n_tiles)]
    ordered["quintile_group"] = np.repeat(np.arange(1, n_tiles + 1), sizes)
    return ordered


def build_period_returns(samples: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    required = {"report_period", "revenue_growth_rate", "SECUCODE", "INNERCODE", "return_20d"}
    missing = required.difference(samples.columns)
    if missing:
        raise ValueError(f"raw samples missing columns: {sorted(missing)}")

    samples = samples.copy()
    samples["report_period"] = normalize_date(samples["report_period"])
    samples["revenue_growth_rate"] = pd.to_numeric(samples["revenue_growth_rate"], errors="coerce")
    samples["return_20d"] = pd.to_numeric(samples["return_20d"], errors="coerce")
    samples = samples.dropna(subset=["report_period", "revenue_growth_rate", "return_20d"])
    if samples.empty:
        raise ValueError("No PIT-valid samples with revenue growth and 20-trading-day returns.")

    ranked_parts = []
    for report_period, panel in samples.groupby("report_period", sort=True):
        ranked_panel = assign_ntile(panel)
        ranked_panel["report_period"] = report_period
        ranked_parts.append(ranked_panel)
    ranked = pd.concat(ranked_parts, ignore_index=True)
    ranked = ranked.dropna(subset=["quintile_group"])
    ranked["quintile_group"] = ranked["quintile_group"].astype(int)

    rows: list[dict[str, object]] = []
    for report_period, panel in ranked.groupby("report_period", sort=True):
        low = panel.loc[panel["quintile_group"] == 1, "return_20d"]
        high = panel.loc[panel["quintile_group"] == 5, "return_20d"]
        if low.empty or high.empty:
            continue
        low_ret = float(low.mean())
        high_ret = float(high.mean())
        rows.append(
            {
                "report_period": pd.Timestamp(report_period).date().isoformat(),
                "sample_count": int(len(panel)),
                "low_count": int(len(low)),
                "high_count": int(len(high)),
                "low_return_pct": round(low_ret * 100, 6),
                "high_return_pct": round(high_ret * 100, 6),
                "long_short_return_pct": round((high_ret - low_ret) * 100, 6),
            }
        )

    period_returns = pd.DataFrame(rows)
    if period_returns.empty:
        raise ValueError("No report period has both the lowest and highest revenue-growth quintiles.")
    return ranked, period_returns


def summarize(period_returns: pd.DataFrame) -> pd.DataFrame:
    long_short = pd.to_numeric(period_returns["long_short_return_pct"], errors="coerce").dropna()
    if long_short.empty:
        raise ValueError("No valid long-short report-period returns.")
    mean_pct = float(long_short.mean())
    std_pct = float(long_short.std(ddof=0))
    return pd.DataFrame(
        [
            {
                "有效报告期数": int(len(long_short)),
                "多空收益均值(%)": round(mean_pct, 2),
                "多空收益标准差(%)": round(std_pct, 2) if not np.isnan(std_pct) else np.nan,
            }
        ],
        columns=RESULT_COLUMNS,
    )


def build_samples() -> pd.DataFrame:
    with shared_connection_session():
        log("fetch stock universe")
        stock_base = fetch_stock_base()
        log(f"stock universe rows: {len(stock_base)}")
        log("fetch first financial disclosures")
        financial_events = fetch_financial_events(stock_base)
        log(f"financial event rows before trading-window filter: {len(financial_events)}")
        log("fetch trading calendar")
        trading_days = fetch_trading_calendar()
        dated_events = add_tracking_dates(financial_events, trading_days)
        if dated_events.empty:
            raise ValueError("No event has 20 subsequent A-share trading days by observe date")
        log(f"event rows with 20 subsequent trading days: {len(dated_events)}")
        log("fetch backward-adjusted prices")
        prices = fetch_prices(dated_events)
        with_returns = attach_returns(dated_events, prices)
        if with_returns.empty:
            raise ValueError("No event has both base and 20th-day backward-adjusted prices")
        log(f"event rows with prices: {len(with_returns)}")
        log("apply listing and ST filters")
        return apply_status_filters(with_returns)


def main() -> pd.DataFrame:
    root = Path(__file__).resolve().parent
    samples = build_samples()
    _, period_returns = build_period_returns(samples)
    result = summarize(period_returns)

    result.to_csv(root / "result.csv", index=False, encoding="utf-8-sig", float_format="%.2f")

    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
