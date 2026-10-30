#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe, shared_connection_session


SIGNAL_START = pd.Timestamp("2025-01-01")
SIGNAL_END_EXCLUSIVE = pd.Timestamp("2026-01-01")
FETCH_START = pd.Timestamp("2023-12-01")
FETCH_END_EXCLUSIVE = pd.Timestamp("2026-01-06")
HOLDING_CURVE_DAYS = (10,)
LOOKBACK_YEARS = 1
LOOKBACK_QUANTILE = 0.90
QUERY_TIMEOUT = 120

AAA_CURVE_CODE = 69
AA_CURVE_CODE = 66
YIELD_TYPE_CODE = 1
TENOR_YEARS = 5

OUTPUT_COLUMNS = [
    "样本数",
    "平均利差变化",
    "中位数利差变化",
    "回到中位数占比",
]


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "样本数": 0,
                "平均利差变化": "",
                "中位数利差变化": "",
                "回到中位数占比": "",
            }
            for horizon in HOLDING_CURVE_DAYS
        ],
        columns=OUTPUT_COLUMNS,
    )


def write_result(result: pd.DataFrame) -> pd.DataFrame:
    result = result.loc[:, OUTPUT_COLUMNS]
    out_path = Path(__file__).with_name("result.csv")
    result.to_csv(out_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_csv(index=False, float_format="%.2f").strip())
    return result


def fetch_curve_series() -> pd.DataFrame:
    sql = f"""
    WITH ranked AS (
        SELECT
            DATE(c.ENDDATE) AS obs_date,
            c.CURVECODE,
            c.YIELD,
            ROW_NUMBER() OVER (
                PARTITION BY DATE(c.ENDDATE), c.CURVECODE
                ORDER BY c.UPDATETIME DESC, c.ID DESC
            ) AS rn
        FROM bond_cbyieldcurve c FORCE INDEX (IX_BOND_CBYIELDCURVE)
        WHERE c.ENDDATE >= '{FETCH_START.date().isoformat()}'
          AND c.ENDDATE < '{FETCH_END_EXCLUSIVE.date().isoformat()}'
          AND c.CURVECODE IN ({AAA_CURVE_CODE}, {AA_CURVE_CODE})
          AND c.YIELDTYPECODE = {YIELD_TYPE_CODE}
          AND c.YEARSTOMATURITY = {TENOR_YEARS}
          AND c.YIELD IS NOT NULL
    )
    SELECT
        obs_date,
        MAX(CASE WHEN CURVECODE = {AAA_CURVE_CODE} THEN YIELD END) AS yield_aaa,
        MAX(CASE WHEN CURVECODE = {AA_CURVE_CODE} THEN YIELD END) AS yield_aa
    FROM ranked
    WHERE rn = 1
    GROUP BY obs_date
    HAVING yield_aaa IS NOT NULL
       AND yield_aa IS NOT NULL
    ORDER BY obs_date;
    """
    raw = query_to_dataframe(sql, max_rows=0, timeout=600)
    if raw.empty:
        return pd.DataFrame(
            columns=["obs_date", "yield_aaa", "yield_aa", "spread_bp"]
        )

    raw["obs_date"] = pd.to_datetime(raw["obs_date"])
    raw["yield_aaa"] = pd.to_numeric(raw["yield_aaa"], errors="coerce")
    raw["yield_aa"] = pd.to_numeric(raw["yield_aa"], errors="coerce")
    raw = raw.dropna(subset=["obs_date", "yield_aaa", "yield_aa"])

    # Verified ChinaBond curve storage is decimal yield; convert the spread to bp.
    raw["spread_bp"] = (raw["yield_aa"] - raw["yield_aaa"]) * 10000.0
    return raw.sort_values("obs_date").reset_index(drop=True)


def add_lookback_thresholds(series: pd.DataFrame) -> pd.DataFrame:
    series = series.sort_values("obs_date").reset_index(drop=True).copy()
    thresholds: list[float | None] = []
    medians: list[float | None] = []
    first_obs_date = series["obs_date"].min()
    for _, row in series.iterrows():
        obs_date = row["obs_date"]
        lookback_start = obs_date - pd.DateOffset(years=LOOKBACK_YEARS)
        if first_obs_date > lookback_start:
            thresholds.append(None)
            medians.append(None)
            continue
        window = series[
            (series["obs_date"] > lookback_start)
            & (series["obs_date"] <= obs_date)
        ]["spread_bp"]
        thresholds.append(float(window.quantile(LOOKBACK_QUANTILE)) if not window.empty else None)
        medians.append(float(window.median()) if not window.empty else None)
    series["p90_1y_bp"] = thresholds
    series["median_1y_bp"] = medians
    return series


def build_signal_detail(series: pd.DataFrame) -> pd.DataFrame:
    if series.empty:
        return pd.DataFrame(
            columns=[
                "signal_date",
                "signal_spread_bp",
                "p90_1y_bp",
                "median_1y_bp",
                "holding_curve_days",
                "exit_date",
                "exit_spread_bp",
                "spread_change_bp",
                "returned_to_median",
            ]
        )

    series = add_lookback_thresholds(series)
    in_signal_window = (series["obs_date"] >= SIGNAL_START) & (
        series["obs_date"] < SIGNAL_END_EXCLUSIVE
    )
    trigger = series["p90_1y_bp"].notna() & (
        series["spread_bp"] >= series["p90_1y_bp"]
    )
    first_trigger = in_signal_window & trigger & ~trigger.shift(fill_value=False)

    rows = []
    for idx in series.index[first_trigger]:
        signal = series.loc[idx]
        for horizon in HOLDING_CURVE_DAYS:
            exit_idx = idx + horizon
            if exit_idx >= len(series):
                continue
            exit_row = series.loc[exit_idx]
            spread_change = float(exit_row["spread_bp"] - signal["spread_bp"])
            rows.append(
                {
                    "signal_date": signal["obs_date"],
                    "signal_spread_bp": float(signal["spread_bp"]),
                    "p90_1y_bp": float(signal["p90_1y_bp"]),
                    "median_1y_bp": float(signal["median_1y_bp"]),
                    "holding_curve_days": horizon,
                    "exit_date": exit_row["obs_date"],
                    "exit_spread_bp": float(exit_row["spread_bp"]),
                    "spread_change_bp": spread_change,
                    "returned_to_median": float(exit_row["spread_bp"])
                    <= float(signal["median_1y_bp"]),
                }
            )
    return pd.DataFrame(rows)


def summarize(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return empty_result()
    rows = []
    for horizon in HOLDING_CURVE_DAYS:
        subset = detail[detail["holding_curve_days"] == horizon].copy()
        changes = pd.to_numeric(subset["spread_change_bp"], errors="coerce").dropna()
        if changes.empty:
            rows.append(
                {
                    "样本数": 0,
                    "平均利差变化": "",
                    "中位数利差变化": "",
                    "回到中位数占比": "",
                }
            )
            continue
        returned = subset.loc[changes.index, "returned_to_median"].astype(bool)
        rows.append(
            {
                "样本数": int(changes.size),
                "平均利差变化": round(float(changes.mean()), 2),
                "中位数利差变化": round(float(changes.median()), 2),
                "回到中位数占比": round(float(returned.mean() * 100.0), 2),
            }
        )
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def main() -> pd.DataFrame:
    with shared_connection_session():
        series = fetch_curve_series()
    detail = build_signal_detail(series)
    return write_result(summarize(detail))


if __name__ == "__main__":
    main()
