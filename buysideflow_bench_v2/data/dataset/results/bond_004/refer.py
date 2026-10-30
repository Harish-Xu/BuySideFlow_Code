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


AS_OF_DATE = pd.Timestamp("2025-04-30")
MATURITIES = [1, 3, 5, 7, 10]
GOV_CURVE_CODE = 10
CDB_CURVE_CODE = 195
OUTPUT_COLUMNS = [
    "期限(年)",
    "当前利差(bp)",
    "过去一年分位(%)",
    "今年以来中位数(bp)",
    "相对中位数偏离(bp)",
]


def render_sql(template: str, **params: str) -> str:
    sql = template
    for key, value in params.items():
        sql = sql.replace(f"{{{{{key}}}}}", value)
    return sql


def load_curve_data() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    template = sql_path.read_text(encoding="utf-8")
    anchor = query_to_dataframe(f"""
        SELECT MAX(curve_date) AS curve_date FROM (
            SELECT DATE(ENDDATE) AS curve_date
            FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
            WHERE CURVECODE IN (10, 195) AND YIELDTYPECODE = 1
              AND YEARSTOMATURITY IN (1, 3, 5, 7, 10) AND YIELD IS NOT NULL
              AND ENDDATE < DATE_ADD('{AS_OF_DATE:%Y-%m-%d}', INTERVAL 1 DAY)
            GROUP BY DATE(ENDDATE)
            HAVING COUNT(DISTINCT CURVECODE, YEARSTOMATURITY) = 10
        ) complete_dates
    """, max_rows=0, timeout=180)
    if anchor.empty or pd.isna(anchor.loc[0, "curve_date"]):
        raise ValueError("No complete curve date available by the observation date.")
    current_date = pd.Timestamp(anchor.loc[0, "curve_date"])
    fetch_start_date = (current_date - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    sql = render_sql(
        template,
        fetch_start_date=fetch_start_date,
        as_of_date=AS_OF_DATE.strftime("%Y-%m-%d"),
    )
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        raise ValueError("No ChinaBond curve rows were returned.")

    df = df.copy()
    df["curve_date"] = pd.to_datetime(df["curve_date"])
    df["curve_code"] = pd.to_numeric(df["curve_code"], errors="raise").astype(int)
    df["maturity_year"] = pd.to_numeric(df["maturity_year"], errors="raise")
    df["yield_value"] = pd.to_numeric(df["yield_value"], errors="raise")
    return df


def build_spread_panel(curve_df: pd.DataFrame) -> pd.DataFrame:
    pivot = (
        curve_df.pivot_table(
            index=["curve_date", "maturity_year"],
            columns="curve_code",
            values="yield_value",
            aggfunc="last",
        )
        .reset_index()
        .rename_axis(None, axis=1)
    )
    required = ["curve_date", "maturity_year", GOV_CURVE_CODE, CDB_CURVE_CODE]
    spread = pivot.dropna(subset=required).copy()
    spread["spread_bp"] = (spread[CDB_CURVE_CODE] - spread[GOV_CURVE_CODE]) * 10000.0
    return spread[["curve_date", "maturity_year", "spread_bp"]]


def resolve_current_curve_date(spread: pd.DataFrame) -> pd.Timestamp:
    complete_dates = (
        spread[spread["maturity_year"].isin(MATURITIES)]
        .groupby("curve_date")["maturity_year"]
        .nunique()
    )
    complete_dates = complete_dates[complete_dates == len(MATURITIES)]
    if complete_dates.empty:
        raise ValueError("No complete curve date found before the as-of date.")
    return pd.Timestamp(complete_dates.index.max())


def percent_rank(history: pd.Series, current_value: float) -> float:
    clean = history.dropna()
    if clean.empty:
        return np.nan
    return float(clean.le(current_value).mean() * 100.0)


def build_result(spread: pd.DataFrame, current_curve_date: pd.Timestamp) -> pd.DataFrame:
    lookback_start = current_curve_date - pd.DateOffset(years=1)
    ytd_start = pd.Timestamp(year=current_curve_date.year, month=1, day=1)
    rows: list[dict[str, float | int | str]] = []

    for maturity in MATURITIES:
        maturity_spread = (
            spread[spread["maturity_year"] == maturity]
            .sort_values("curve_date")
            .reset_index(drop=True)
        )
        current_match = maturity_spread[
            maturity_spread["curve_date"] == current_curve_date
        ]
        if current_match.empty:
            raise ValueError(f"Missing current spread for maturity {maturity}Y.")

        current_spread = float(current_match["spread_bp"].iloc[0])
        history = maturity_spread[
            (maturity_spread["curve_date"] >= lookback_start)
            & (maturity_spread["curve_date"] <= current_curve_date)
        ]["spread_bp"]
        ytd = maturity_spread[
            (maturity_spread["curve_date"] >= ytd_start)
            & (maturity_spread["curve_date"] <= current_curve_date)
        ]["spread_bp"]
        if ytd.empty:
            raise ValueError(f"Missing YTD spread sample for maturity {maturity}Y.")

        ytd_median = float(ytd.median())
        rows.append(
            {
                "期限(年)": maturity,
                "当前利差(bp)": current_spread,
                "过去一年分位(%)": percent_rank(history, current_spread),
                "今年以来中位数(bp)": ytd_median,
                "相对中位数偏离(bp)": current_spread - ytd_median,
            }
        )

    result = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    for col in OUTPUT_COLUMNS[1:]:
        result[col] = result[col].map(lambda x: f"{x:.2f}")
    return result


def main() -> pd.DataFrame:
    curve_df = load_curve_data()
    spread = build_spread_panel(curve_df)
    current_curve_date = resolve_current_curve_date(spread)
    result = build_result(spread, current_curve_date)
    output_path = Path(__file__).with_name("result.csv")
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"current_curve_date={current_curve_date.date()}")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
