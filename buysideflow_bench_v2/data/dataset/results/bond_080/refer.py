#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


MONTH_START = pd.Period("2024-01", freq="M")
MONTH_END = pd.Period("2025-12", freq="M")
WINDOW_DAYS = 5
FORWARD_DAYS = 3
TENOR_COLUMNS = {
    "1Y": "gov_1y_yield",
    "3Y": "gov_3y_yield",
    "10Y": "gov_10y_yield",
}

RESULT_COLUMNS = [
    "月份",
    "期限",
    "FR007变化(bp)",
    "3日后国债收益率变化(bp)",
    "beta",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="2024-2025 月末 FR007 变化后的国债曲线 3 日反应")
    parser.add_argument("--output-path", default=str(Path(__file__).with_name("result.csv")))
    return parser.parse_args()


def load_common_series() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    if df.empty:
        return pd.DataFrame(columns=["obs_date", "fr007_pct", *TENOR_COLUMNS.values()])

    data = df.copy()
    data["obs_date"] = pd.to_datetime(data["obs_date"])
    data["fr007_pct"] = pd.to_numeric(data["fr007_pct"], errors="coerce")
    for col in TENOR_COLUMNS.values():
        data[col] = pd.to_numeric(data[col], errors="coerce")

    return (
        data.dropna(subset=["obs_date", "fr007_pct", *TENOR_COLUMNS.values()])
        .sort_values("obs_date")
        .drop_duplicates("obs_date", keep="last")
        .reset_index(drop=True)
    )


def build_result(series: pd.DataFrame) -> pd.DataFrame:
    if series.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    data = series.copy()
    data["month_period"] = data["obs_date"].dt.to_period("M")
    date_to_pos = {pd.Timestamp(date): pos for pos, date in enumerate(data["obs_date"])}

    rows: list[dict[str, object]] = []
    for month in pd.period_range(MONTH_START, MONTH_END, freq="M"):
        monthly = data[data["month_period"].eq(month)]
        window = monthly.tail(WINDOW_DAYS)
        if len(window) < WINDOW_DAYS:
            continue

        start = window.iloc[0]
        end = window.iloc[-1]
        end_pos = date_to_pos.get(pd.Timestamp(end["obs_date"]))
        if end_pos is None:
            continue

        forward_pos = end_pos + FORWARD_DAYS
        if forward_pos >= len(data):
            continue
        forward = data.iloc[forward_pos]

        fr007_change_bp = (float(end["fr007_pct"]) - float(start["fr007_pct"])) * 100.0
        for tenor, yield_col in TENOR_COLUMNS.items():
            gov_change_bp = (float(forward[yield_col]) - float(end[yield_col])) * 10000.0
            beta = gov_change_bp / fr007_change_bp if abs(fr007_change_bp) >= 1.0 else pd.NA
            rows.append(
                {
                    "月份": month.strftime("%Y-%m"),
                    "期限": tenor,
                    "FR007变化(bp)": fr007_change_bp,
                    "3日后国债收益率变化(bp)": gov_change_bp,
                    "beta": beta,
                    "tenor_sort": int(tenor.rstrip("Y")),
                }
            )

    if not rows:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    result = pd.DataFrame(rows).sort_values(["月份", "tenor_sort"]).reset_index(drop=True)
    return result.drop(columns=["tenor_sort"])


def format_result(result: pd.DataFrame) -> pd.DataFrame:
    if result.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    def format_number(value: object) -> str:
        number = pd.to_numeric(value, errors="coerce")
        if pd.isna(number):
            return ""
        number = 0.0 if abs(float(number)) < 0.00005 else float(number)
        return f"{number:.4f}"

    formatted = result.copy()
    for col in ["FR007变化(bp)", "3日后国债收益率变化(bp)", "beta"]:
        formatted[col] = formatted[col].map(format_number)
    return formatted[RESULT_COLUMNS]


def main() -> None:
    args = parse_args()
    series = load_common_series()
    result = format_result(build_result(series))
    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
