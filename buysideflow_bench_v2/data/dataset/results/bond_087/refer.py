#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


QUOTE_END_DATE = "2026-01-30"
DATA_CUTOFF_TS = "2026-01-31 00:00:00"
CURVE_LOOKBACK_DAYS = 45
EVENT_LOOKAHEAD_DAYS = 60
HOLDING_DAYS = 5
CHUNK_SIZE = 800

RESULT_COLUMNS = [
    "样本数",
    "平均发行偏离(bp)",
    "5日平均全价变动率(%)",
    "胜率(%)",
]


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def sql_list(values) -> str:
    return ",".join(str(int(v)) for v in values if pd.notna(v))


def chunks(values, size: int = CHUNK_SIZE):
    values = sorted({int(v) for v in values if pd.notna(v)})
    for i in range(0, len(values), size):
        yield values[i : i + size]


def load_candidate_issues() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    for col in ["MAINCODE", "INNERCODE", "SECUMARKET", "curve_code"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in [
        "issue_maturity_years",
        "issue_yield_pct",
        "issue_ref_ytm_pct",
        "coupon_rate_pct",
        "issue_price",
        "par_value",
        "RATEIFFLOAT",
        "COMPOUNDMETHOD",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["issue_start_date", "listed_date"]:
        df[col] = pd.to_datetime(df[col], errors="coerce")

    df = df.dropna(
        subset=[
            "MAINCODE",
            "INNERCODE",
            "SECUMARKET",
            "bond_type",
            "curve_code",
            "issue_start_date",
            "listed_date",
            "issue_maturity_years",
            "issue_yield_pct",
        ]
    ).copy()
    if df.empty:
        return df

    df["MAINCODE"] = df["MAINCODE"].astype("int64")
    df["INNERCODE"] = df["INNERCODE"].astype("int64")
    df["SECUMARKET"] = df["SECUMARKET"].astype("int64")
    df["curve_code"] = df["curve_code"].astype("int64")
    return df.reset_index(drop=True)


def fetch_curve_points(issues: pd.DataFrame) -> pd.DataFrame:
    if issues.empty:
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    keys = issues[["curve_code", "issue_start_date"]].drop_duplicates()
    for _, key in keys.iterrows():
        curve_code = int(key["curve_code"])
        issue_date = pd.Timestamp(key["issue_start_date"]).date().isoformat()
        sql = f"""
        WITH ref AS (
            SELECT MAX(ENDDATE) AS ref_enddate
            FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
            WHERE ENDDATE < DATE('{issue_date}')
              AND ENDDATE >= DATE_SUB(DATE('{issue_date}'), INTERVAL {CURVE_LOOKBACK_DAYS} DAY)
              AND CURVECODE = {curve_code}
              AND YIELDTYPECODE = 1
              AND YEARSTOMATURITY BETWEEN 0 AND 10
              AND YIELD IS NOT NULL
        ),
        curve_ranked AS (
            SELECT
                DATE(y.ENDDATE) AS curve_date,
                y.CURVECODE AS curve_code,
                y.YEARSTOMATURITY AS tenor_years,
                y.YIELD AS curve_yield,
                ROW_NUMBER() OVER (
                    PARTITION BY DATE(y.ENDDATE), y.CURVECODE, y.YEARSTOMATURITY
                    ORDER BY y.UPDATETIME DESC, y.ID DESC
                ) AS rn
            FROM bond_cbyieldcurve y FORCE INDEX (IX_BOND_CBYIELDCURVE)
            JOIN ref r
              ON r.ref_enddate IS NOT NULL
             AND y.ENDDATE = r.ref_enddate
            WHERE y.CURVECODE = {curve_code}
              AND y.YIELDTYPECODE = 1
              AND y.YEARSTOMATURITY BETWEEN 0 AND 10
              AND y.YIELD IS NOT NULL
        )
        SELECT
            curve_date,
            curve_code,
            tenor_years,
            curve_yield
        FROM curve_ranked
        WHERE rn = 1
        ORDER BY curve_code, curve_date, tenor_years;
        """
        frame = query_to_dataframe(sql, max_rows=0, timeout=120)
        if frame is not None and not frame.empty:
            frames.append(frame)

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if df.empty:
        return pd.DataFrame()

    df = df.copy()
    df["curve_date"] = pd.to_datetime(df["curve_date"], errors="coerce")
    for col in ["curve_code", "tenor_years", "curve_yield"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["curve_date", "curve_code", "tenor_years", "curve_yield"]).copy()
    df["curve_code"] = df["curve_code"].astype("int64")
    return df.reset_index(drop=True)


def interpolate_curve_yield_pct(points: pd.DataFrame, tenor: float) -> float:
    curve = (
        points[["tenor_years", "curve_yield"]]
        .dropna()
        .drop_duplicates("tenor_years", keep="last")
        .sort_values("tenor_years")
    )
    tenors = curve["tenor_years"].to_numpy(dtype=float)
    yields = curve["curve_yield"].to_numpy(dtype=float)
    if len(tenors) == 0 or tenor < tenors.min() or tenor > tenors.max():
        return np.nan
    return float(np.interp(tenor, tenors, yields) * 100.0)


def attach_primary_concession(issues: pd.DataFrame, curves: pd.DataFrame) -> pd.DataFrame:
    if issues.empty or curves.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    curve_groups = {
        int(code): group.sort_values(["curve_date", "tenor_years"]).copy()
        for code, group in curves.groupby("curve_code")
    }

    for _, issue in issues.iterrows():
        curve = curve_groups.get(int(issue["curve_code"]))
        if curve is None or curve.empty:
            continue

        available_dates = np.array(sorted(curve["curve_date"].dropna().unique()))
        idx = available_dates.searchsorted(issue["issue_start_date"].to_datetime64(), side="left") - 1
        if idx < 0:
            continue
        ref_date = pd.Timestamp(available_dates[idx])
        ref_points = curve[curve["curve_date"] == ref_date]
        curve_yield_pct = interpolate_curve_yield_pct(
            ref_points, float(issue["issue_maturity_years"])
        )
        if pd.isna(curve_yield_pct):
            continue

        row = issue.to_dict()
        row["curve_ref_date"] = ref_date
        row["curve_yield_pct"] = curve_yield_pct
        row["primary_concession_bp"] = (
            float(issue["issue_yield_pct"]) - curve_yield_pct
        ) * 100.0
        rows.append(row)

    return pd.DataFrame(rows)


def fetch_quotes(signals: pd.DataFrame) -> pd.DataFrame:
    if signals.empty:
        return pd.DataFrame()

    quote_start = signals["listed_date"].min().date().isoformat()
    quote_end = min(
        signals["listed_date"].max() + pd.Timedelta(days=EVENT_LOOKAHEAD_DAYS),
        pd.Timestamp(QUOTE_END_DATE),
    ).date().isoformat()
    frames: list[pd.DataFrame] = []
    for part in chunks(signals["INNERCODE"], size=300):
        sql = f"""
        SELECT
            CAST(INNERCODE AS UNSIGNED) AS INNERCODE,
            DATE(TRADINGDAY) AS trade_date,
            CLOSEDIRTYPRICE AS full_price,
            INSERTTIME,
            UPDATETIME,
            ID
        FROM bond_basicquote
        WHERE INNERCODE IN ({sql_list(part)})
          AND SECUMARKET = 89
          AND TRADINGDAY >= DATE('{quote_start}')
          AND TRADINGDAY <= DATE('{quote_end}')
          AND INSERTTIME < TIMESTAMP('{DATA_CUTOFF_TS}')
          AND CLOSEDIRTYPRICE IS NOT NULL
          AND CLOSEDIRTYPRICE > 0;
        """
        frame = query_to_dataframe(sql, max_rows=0, timeout=600)
        if frame is not None and not frame.empty:
            frames.append(frame)

    raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if raw.empty:
        return raw

    raw = raw.copy()
    raw["INNERCODE"] = pd.to_numeric(raw["INNERCODE"], errors="coerce")
    raw["trade_date"] = pd.to_datetime(raw["trade_date"], errors="coerce")
    raw["full_price"] = pd.to_numeric(raw["full_price"], errors="coerce")
    raw["INSERTTIME"] = pd.to_datetime(raw["INSERTTIME"], errors="coerce")
    raw["UPDATETIME"] = pd.to_datetime(raw["UPDATETIME"], errors="coerce")
    raw["ID"] = pd.to_numeric(raw["ID"], errors="coerce")
    raw = raw.dropna(subset=["INNERCODE", "trade_date", "full_price"]).copy()
    raw["INNERCODE"] = raw["INNERCODE"].astype("int64")

    signal_dates = signals[["MAINCODE", "INNERCODE", "listed_date"]].drop_duplicates()
    mapped = raw.merge(signal_dates, on="INNERCODE", how="inner")
    mapped = mapped[
        (mapped["trade_date"] >= mapped["listed_date"])
        & (mapped["trade_date"] <= mapped["listed_date"] + pd.Timedelta(days=EVENT_LOOKAHEAD_DAYS))
    ].copy()
    if mapped.empty:
        return mapped

    mapped = mapped.sort_values(
        ["MAINCODE", "trade_date", "UPDATETIME", "INSERTTIME", "ID"],
        ascending=[True, True, False, False, False],
    )
    dedup = mapped.drop_duplicates(["MAINCODE", "trade_date"], keep="first").copy()
    return dedup.sort_values(["MAINCODE", "trade_date"]).reset_index(drop=True)


def fetch_trade_calendar(signals: pd.DataFrame) -> pd.DataFrame:
    if signals.empty:
        return pd.DataFrame()

    quote_start = signals["listed_date"].min().date().isoformat()
    quote_end = min(
        signals["listed_date"].max() + pd.Timedelta(days=EVENT_LOOKAHEAD_DAYS),
        pd.Timestamp(QUOTE_END_DATE),
    ).date().isoformat()
    markets = sql_list(signals["SECUMARKET"].dropna().unique())
    if not markets:
        return pd.DataFrame()

    sql = f"""
    SELECT
        CAST(SECUMARKET AS UNSIGNED) AS SECUMARKET,
        DATE(TRADINGDATE) AS trade_date
    FROM qt_tradingdaynew
    WHERE SECUMARKET IN ({markets})
      AND TRADINGDATE >= DATE('{quote_start}')
      AND TRADINGDATE <= DATE('{quote_end}')
      AND IFTRADINGDAY = 1
    ORDER BY SECUMARKET, TRADINGDATE;
    """
    calendar = query_to_dataframe(sql, max_rows=0, timeout=120)
    if calendar is None or calendar.empty:
        return pd.DataFrame()

    calendar = calendar.copy()
    calendar["SECUMARKET"] = pd.to_numeric(calendar["SECUMARKET"], errors="coerce")
    calendar["trade_date"] = pd.to_datetime(calendar["trade_date"], errors="coerce")
    calendar = calendar.dropna(subset=["SECUMARKET", "trade_date"]).copy()
    calendar["SECUMARKET"] = calendar["SECUMARKET"].astype("int64")
    return calendar.drop_duplicates(["SECUMARKET", "trade_date"]).reset_index(drop=True)


def build_event_returns(
    signals: pd.DataFrame, quotes: pd.DataFrame, calendar: pd.DataFrame
) -> pd.DataFrame:
    if signals.empty or quotes.empty or calendar.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    quote_groups = {
        int(maincode): group.sort_values("trade_date").reset_index(drop=True)
        for maincode, group in quotes.groupby("MAINCODE")
    }
    calendar_groups = {
        int(market): group.sort_values("trade_date").reset_index(drop=True)
        for market, group in calendar.groupby("SECUMARKET")
    }

    for _, signal in signals.iterrows():
        q = quote_groups.get(int(signal["MAINCODE"]))
        cal = calendar_groups.get(int(signal["SECUMARKET"]))
        if q is None or cal is None:
            continue

        # T0 is the first bank trading day on or after the listing date;
        # T5 is the fifth subsequent bank trading day.  The return only
        # needs the two endpoint prices, so missing intermediate quotes do
        # not invalidate an otherwise observable endpoint-to-endpoint return.
        valid_trade_dates = cal.loc[
            cal["trade_date"] >= signal["listed_date"], ["trade_date"]
        ].head(HOLDING_DAYS + 1)
        if len(valid_trade_dates) < HOLDING_DAYS + 1:
            continue

        endpoint_dates = valid_trade_dates.iloc[[0, HOLDING_DAYS]]
        window = endpoint_dates.merge(q, on="trade_date", how="left")
        if len(window) != 2 or window["full_price"].isna().any():
            continue

        entry = window.iloc[0]
        exit_row = window.iloc[1]
        holding_return_pct = (
            float(exit_row["full_price"]) / float(entry["full_price"]) - 1.0
        ) * 100.0
        row = signal.to_dict()
        row["entry_date"] = entry["trade_date"]
        row["exit_date"] = exit_row["trade_date"]
        row["entry_full_price"] = float(entry["full_price"])
        row["exit_full_price"] = float(exit_row["full_price"])
        row["holding_return_pct"] = holding_return_pct
        rows.append(row)

    return pd.DataFrame(rows)


def select_high_concession(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return events

    ranked = events.sort_values(
        ["primary_concession_bp", "issue_start_date", "MAINCODE"],
        ascending=[False, True, True],
    ).reset_index(drop=True)
    if len(ranked) == 1:
        ranked["concession_percent_rank"] = 0.0
    else:
        metric_rank = ranked["primary_concession_bp"].rank(method="min", ascending=False)
        ranked["concession_percent_rank"] = (metric_rank - 1.0) / (len(ranked) - 1.0)
    return ranked[ranked["concession_percent_rank"] <= 0.30].copy()


def summarize(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return empty_result()

    summary = pd.DataFrame(
        [
            {
                "样本数": int(len(events)),
                "平均发行偏离(bp)": events["primary_concession_bp"].mean(),
                "5日平均全价变动率(%)": events["holding_return_pct"].mean(),
                "胜率(%)": (events["holding_return_pct"] > 0).mean() * 100.0,
            }
        ]
    )
    summary["样本数"] = summary["样本数"].astype(int)
    for col in ["平均发行偏离(bp)", "5日平均全价变动率(%)", "胜率(%)"]:
        summary[col] = summary[col].round(2)
    return summary[RESULT_COLUMNS].reset_index(drop=True)


def main() -> pd.DataFrame:
    issues = load_candidate_issues()
    if issues.empty:
        return empty_result()

    curves = fetch_curve_points(issues)
    signals = attach_primary_concession(issues, curves)
    if signals.empty:
        return empty_result()

    selected = select_high_concession(signals)
    if selected.empty:
        return empty_result()

    quotes = fetch_quotes(selected)
    calendar = fetch_trade_calendar(selected)
    events = build_event_returns(selected, quotes, calendar)
    return summarize(events)


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent)
    result = main()
    result.to_csv("result.csv", index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_string(index=False))
