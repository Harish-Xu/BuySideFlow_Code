#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


RESULT_COLUMNS = [
    "起始截面日",
    "结束截面日",
    "持续周数",
    "3年累计收窄",
    "5年累计收窄",
    "10年累计收窄",
]

PERIOD_START = pd.Timestamp("2023-01-01")
PERIOD_END = pd.Timestamp("2025-01-01")


def week_label(timestamp: pd.Timestamp) -> str:
    iso = timestamp.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def build_weekly_spreads(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        raise RuntimeError("SQL returned no curve spread observations")

    raw = raw.copy()
    raw["obs_date"] = pd.to_datetime(raw["obs_date"])
    raw["spread_bp"] = pd.to_numeric(raw["spread_bp"], errors="coerce")
    wide = (
        raw.pivot_table(index="obs_date", columns="term", values="spread_bp", aggfunc="last")
        .dropna(subset=["3Y", "5Y", "10Y"])
        .sort_index()
    )
    if wide.empty:
        raise RuntimeError("No dates have all 3Y/5Y/10Y local-government spread observations")

    daily = wide.reset_index()
    daily["natural_week"] = daily["obs_date"].dt.to_period("W-SUN")
    weekly = (
        daily.sort_values("obs_date")
        .groupby("natural_week", as_index=False)
        .tail(1)
        .sort_values("obs_date")
        .reset_index(drop=True)
    )
    weekly["week_label"] = weekly["obs_date"].map(week_label)
    return weekly[["natural_week", "week_label", "obs_date", "3Y", "5Y", "10Y"]]


def find_shrink_runs(weekly: pd.DataFrame) -> pd.DataFrame:
    analysis = weekly[weekly["obs_date"] < PERIOD_END].copy()
    if len(analysis) < 2:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    diffs = analysis[["3Y", "5Y", "10Y"]].diff()
    adjacent_week = analysis["natural_week"].astype("int64").diff().eq(1)
    analysis["sync_shrink"] = (
        adjacent_week
        & diffs.lt(0).all(axis=1)
        & analysis["obs_date"].ge(PERIOD_START)
    )
    period_analysis = analysis[analysis["obs_date"].ge(PERIOD_START)]

    runs: list[dict[str, object]] = []
    current_start_idx: int | None = None
    for idx, row in period_analysis.iterrows():
        if bool(row["sync_shrink"]):
            if current_start_idx is None:
                current_start_idx = idx
        elif current_start_idx is not None:
            runs.append(build_run(analysis, current_start_idx, idx - 1))
            current_start_idx = None
    if current_start_idx is not None:
        runs.append(build_run(analysis, current_start_idx, analysis.index[-1]))

    if not runs:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    result = pd.DataFrame(runs)
    result = result[result["持续周数"] >= 2]
    result = result.sort_values(
        ["合计收窄", "持续周数", "起始截面日"],
        ascending=[False, False, True],
    ).head(3)
    return result[RESULT_COLUMNS].reset_index(drop=True)


def build_run(analysis: pd.DataFrame, start_idx: int, end_idx: int) -> dict[str, object]:
    prev_idx = analysis.index[analysis.index.get_loc(start_idx) - 1]
    start_row = analysis.loc[start_idx]
    end_row = analysis.loc[end_idx]
    prev_row = analysis.loc[prev_idx]

    shrink_3y = float(prev_row["3Y"] - end_row["3Y"])
    shrink_5y = float(prev_row["5Y"] - end_row["5Y"])
    shrink_10y = float(prev_row["10Y"] - end_row["10Y"])
    return {
        "起始截面日": start_row["obs_date"].strftime("%Y-%m-%d"),
        "结束截面日": end_row["obs_date"].strftime("%Y-%m-%d"),
        "持续周数": int(analysis.index.get_loc(end_idx) - analysis.index.get_loc(start_idx) + 1),
        "3年累计收窄": shrink_3y,
        "5年累计收窄": shrink_5y,
        "10年累计收窄": shrink_10y,
        "合计收窄": shrink_3y + shrink_5y + shrink_10y,
    }


def format_result(result: pd.DataFrame) -> pd.DataFrame:
    formatted = result.copy()
    for column in ["3年累计收窄", "5年累计收窄", "10年累计收窄"]:
        formatted[column] = formatted[column].map(lambda value: f"{float(value):.2f}")
    if not formatted.empty:
        formatted["持续周数"] = formatted["持续周数"].astype(int)
    return formatted[RESULT_COLUMNS]


def main() -> pd.DataFrame:
    base_dir = Path(__file__).resolve().parent
    sql = (base_dir / "refer.sql").read_text(encoding="utf-8")
    raw = query_to_dataframe(sql, max_rows=0, timeout=300)
    if raw is None:
        raise RuntimeError("SQL returned None")

    result = format_result(find_shrink_runs(build_weekly_spreads(raw)))
    result.to_csv(base_dir / "result.csv", index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
