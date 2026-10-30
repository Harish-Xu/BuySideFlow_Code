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


AS_OF_DATE = pd.Timestamp("2025-12-30")
AA_PLUS_CURVE_CODE = 68
AAA_CURVE_CODE = 69
OUTPUT_COLUMNS = [
    "观察日",
    "当前曲线日",
    "当前利差(bp)",
    "近一年分位",
    "近一年均值(bp)",
    "近一年标准差(bp)",
    "相对均值z-score",
    "近1个月基准日",
    "近1个月利差变化(bp)",
    "近一年有效样本数",
]


def render_sql(template: str, **params: str) -> str:
    sql = template
    for key, value in params.items():
        sql = sql.replace(f"{{{{{key}}}}}", value)
    return sql


def load_curve_data() -> pd.DataFrame:
    template = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    sql = render_sql(template, as_of_date=AS_OF_DATE.strftime("%Y-%m-%d"))
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        raise ValueError("No ChinaBond medium-term note curve rows were returned.")

    df = df.copy()
    df["curve_date"] = pd.to_datetime(df["curve_date"])
    df["curve_code"] = pd.to_numeric(df["curve_code"], errors="raise").astype(int)
    df["yield_value"] = pd.to_numeric(df["yield_value"], errors="raise")
    return df


def build_spread_panel(curve_df: pd.DataFrame) -> pd.DataFrame:
    pivot = (
        curve_df.pivot_table(
            index="curve_date",
            columns="curve_code",
            values="yield_value",
            aggfunc="last",
        )
        .reset_index()
        .rename_axis(None, axis=1)
    )
    complete = pivot.dropna(subset=[AA_PLUS_CURVE_CODE, AAA_CURVE_CODE]).copy()
    complete["spread_bp"] = (
        complete[AA_PLUS_CURVE_CODE] - complete[AAA_CURVE_CODE]
    ) * 10000.0
    return complete.sort_values("curve_date").reset_index(drop=True)


def empirical_percentile(history: pd.Series, current_value: float) -> float:
    clean = history.dropna()
    if clean.empty:
        return np.nan
    return float(clean.le(current_value).mean())


def resolve_current_date(spread: pd.DataFrame) -> pd.Timestamp:
    eligible = spread[spread["curve_date"] <= AS_OF_DATE]
    if eligible.empty:
        raise ValueError("No complete AA+/AAA curve date found before as-of date.")
    return pd.Timestamp(eligible["curve_date"].max())


def resolve_one_month_base_date(spread: pd.DataFrame, current_date: pd.Timestamp) -> pd.Timestamp:
    anchor = current_date - pd.DateOffset(months=1)
    eligible = spread[spread["curve_date"] <= anchor]
    if eligible.empty:
        raise ValueError("No spread observation found before the one-month anchor date.")
    return pd.Timestamp(eligible["curve_date"].max())


def build_output(spread: pd.DataFrame) -> pd.DataFrame:
    current_date = resolve_current_date(spread)
    lookback_start = current_date - pd.DateOffset(years=1)
    hist = spread[
        (spread["curve_date"] >= lookback_start)
        & (spread["curve_date"] <= current_date)
    ].copy()
    if hist.empty:
        raise ValueError("No one-year spread sample after complete-curve filtering.")

    current_spread = float(hist.loc[hist["curve_date"] == current_date, "spread_bp"].iloc[0])
    mean_1y = float(hist["spread_bp"].mean())
    std_1y = float(hist["spread_bp"].std(ddof=0))
    z_score = np.nan if std_1y == 0 or np.isnan(std_1y) else (current_spread - mean_1y) / std_1y
    percentile = empirical_percentile(hist["spread_bp"], current_spread)

    base_date = resolve_one_month_base_date(spread, current_date)
    base_spread = float(spread.loc[spread["curve_date"] == base_date, "spread_bp"].iloc[0])
    one_month_change = current_spread - base_spread

    summary = pd.DataFrame(
        [
            {
                "观察日": AS_OF_DATE.strftime("%Y-%m-%d"),
                "当前曲线日": current_date.strftime("%Y-%m-%d"),
                "当前利差(bp)": current_spread,
                "近一年分位": percentile,
                "近一年均值(bp)": mean_1y,
                "近一年标准差(bp)": std_1y,
                "相对均值z-score": z_score,
                "近1个月基准日": base_date.strftime("%Y-%m-%d"),
                "近1个月利差变化(bp)": one_month_change,
                "近一年有效样本数": int(len(hist)),
            }
        ],
        columns=OUTPUT_COLUMNS,
    )

    return summary


def format_summary(summary: pd.DataFrame) -> pd.DataFrame:
    formatted = summary.copy()
    for col in [
        "当前利差(bp)",
        "近一年分位",
        "近一年均值(bp)",
        "近一年标准差(bp)",
        "相对均值z-score",
        "近1个月利差变化(bp)",
    ]:
        formatted[col] = formatted[col].map(lambda x: "" if pd.isna(x) else f"{x:.2f}")
    return formatted


def main() -> pd.DataFrame:
    curve_df = load_curve_data()
    spread = build_spread_panel(curve_df)
    if spread.empty:
        raise ValueError("No date has both AA+ and AAA 5Y curve points.")

    summary = format_summary(build_output(spread))

    output_dir = Path(__file__).resolve().parent
    summary.to_csv(output_dir / "result.csv", index=False, encoding="utf-8-sig")

    print("result.csv")
    print(summary.to_csv(index=False).strip())
    return summary


if __name__ == "__main__":
    main()
