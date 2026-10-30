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


AS_OF_DATE = pd.Timestamp("2025-12-31")
SAMPLE_START = pd.Timestamp("2025-01-01")
HISTORY_YEARS = 3
HISTORY_START = SAMPLE_START - pd.DateOffset(years=HISTORY_YEARS)
MATURITIES = [2, 5, 10]
OUTPUT_COLUMNS = [
    "记录类型",
    "起始日期",
    "结束日期",
    "持续曲线日数",
    "区间最高凹凸度(bp)",
    "最高值日期",
    "对应分位(%)",
]


def render_sql(template: str, **params: str) -> str:
    sql = template
    for key, value in params.items():
        sql = sql.replace(f"{{{{{key}}}}}", value)
    return sql


def load_curve_data() -> pd.DataFrame:
    template = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    sql = render_sql(
        template,
        history_start=HISTORY_START.strftime("%Y-%m-%d"),
        as_of_date=AS_OF_DATE.strftime("%Y-%m-%d"),
    )
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        raise ValueError("No ChinaBond government curve rows were returned.")

    df = df.copy()
    df["curve_date"] = pd.to_datetime(df["curve_date"])
    df["maturity_year"] = pd.to_numeric(df["maturity_year"], errors="raise").astype(int)
    df["yield_value"] = pd.to_numeric(df["yield_value"], errors="raise")
    return df


def build_curvature_panel(curve_df: pd.DataFrame) -> pd.DataFrame:
    pivot = (
        curve_df.pivot_table(
            index="curve_date",
            columns="maturity_year",
            values="yield_value",
            aggfunc="last",
        )
        .reset_index()
        .rename_axis(None, axis=1)
    )
    complete = pivot.dropna(subset=MATURITIES).copy()
    complete["curvature_bp"] = ((complete[2] + complete[10]) / 2.0 - complete[5]) * 10000.0
    complete = complete.sort_values("curve_date").reset_index(drop=True)
    percentiles: list[float] = []
    history_counts: list[int] = []
    for row in complete.itertuples(index=False):
        history_start = row.curve_date - pd.DateOffset(years=HISTORY_YEARS)
        history = complete.loc[
            (complete["curve_date"] >= history_start)
            & (complete["curve_date"] < row.curve_date),
            "curvature_bp",
        ]
        history_counts.append(int(history.notna().sum()))
        percentiles.append(rolling_percent_rank(history, row.curvature_bp))
    complete["rolling_percentile"] = percentiles
    complete["history_sample_count"] = history_counts
    return complete[
        ["curve_date", "curvature_bp", "rolling_percentile", "history_sample_count"]
    ]


def rolling_percent_rank(history: pd.Series, current_value: float) -> float:
    clean = history.dropna()
    if clean.empty:
        return np.nan
    return float(clean.le(current_value).mean() * 100.0)


def build_abnormal_intervals(panel: pd.DataFrame) -> pd.DataFrame:
    panel = panel.copy()
    panel["obs_pos"] = np.arange(len(panel))
    sample = panel[
        (panel["curve_date"] >= SAMPLE_START)
        & (panel["curve_date"] <= AS_OF_DATE)
        & (panel["rolling_percentile"] >= 95.0)
    ].copy()
    if sample.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    sample["group_id"] = (
        sample["obs_pos"].diff().ne(1).fillna(True).cumsum()
    )
    rows: list[dict[str, object]] = []
    for _, group in sample.groupby("group_id", sort=True):
        group = group.sort_values("curve_date")
        peak_idx = group["curvature_bp"].idxmax()
        peak = group.loc[peak_idx]
        rows.append(
            {
                "记录类型": "异常区间",
                "起始日期": group["curve_date"].iloc[0].strftime("%Y-%m-%d"),
                "结束日期": group["curve_date"].iloc[-1].strftime("%Y-%m-%d"),
                "持续曲线日数": int(len(group)),
                "区间最高凹凸度(bp)": float(peak["curvature_bp"]),
                "最高值日期": peak["curve_date"].strftime("%Y-%m-%d"),
                "对应分位(%)": float(peak["rolling_percentile"]),
            }
        )
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def append_latest_curve_row(result: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    latest_date = panel["curve_date"].max()
    latest = panel.loc[panel["curve_date"] == latest_date].iloc[0]
    latest_row = pd.DataFrame(
        [
            {
                "记录类型": "最新曲线日",
                "起始日期": latest_date.strftime("%Y-%m-%d"),
                "结束日期": latest_date.strftime("%Y-%m-%d"),
                "持续曲线日数": 1,
                "区间最高凹凸度(bp)": float(latest["curvature_bp"]),
                "最高值日期": latest_date.strftime("%Y-%m-%d"),
                "对应分位(%)": float(latest["rolling_percentile"]),
            }
        ],
        columns=OUTPUT_COLUMNS,
    )
    return pd.concat([result, latest_row], ignore_index=True)


def format_result(result: pd.DataFrame) -> pd.DataFrame:
    formatted = result.copy()
    for col in ["区间最高凹凸度(bp)", "对应分位(%)"]:
        formatted[col] = formatted[col].map(lambda x: "" if pd.isna(x) else f"{x:.2f}")
    return formatted


def main() -> pd.DataFrame:
    curve_df = load_curve_data()
    panel = build_curvature_panel(curve_df)
    if panel.empty:
        raise ValueError("No complete 2Y/5Y/10Y government curve dates found.")

    result = build_abnormal_intervals(panel)
    result = append_latest_curve_row(result, panel)
    result = format_result(result)
    output_path = Path(__file__).with_name("result.csv")
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
