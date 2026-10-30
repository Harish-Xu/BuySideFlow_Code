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


TARGET_TENORS = [1.0, 3.0, 5.0, 10.0]
WINDOW_DAYS = 5

GOV_CURVE_CODE = 10
CDB_CURVE_CODE = 195
LOCAL_GOV_CURVE_CODE = 199

CURVE_LABELS = {
    GOV_CURVE_CODE: "国债",
    CDB_CURVE_CODE: "国开债",
    LOCAL_GOV_CURVE_CODE: "地方债",
}

RESULT_COLUMNS = [
    "月份",
    "期限",
    "地方债-国债期初利差(bp)",
    "地方债-国债期末利差(bp)",
    "地方债-国债利差变化(bp)",
    "地方债-国开债期初利差(bp)",
    "地方债-国开债期末利差(bp)",
    "地方债-国开债利差变化(bp)",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="2025 年月末地方债曲线相对国债、国开债曲线利差变化")
    parser.add_argument("--output-path", default=str(Path(__file__).with_name("result.csv")))
    return parser.parse_args()


def load_curve_points() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    if df.empty:
        return pd.DataFrame(columns=["obs_date", "month", "CURVECODE", "YEARSTOMATURITY", "YIELD"])

    data = df.copy()
    data["obs_date"] = pd.to_datetime(data["obs_date"])
    data["CURVECODE"] = pd.to_numeric(data["CURVECODE"], errors="coerce").astype("Int64")
    data["YEARSTOMATURITY"] = pd.to_numeric(data["YEARSTOMATURITY"], errors="coerce")
    data["YIELD"] = pd.to_numeric(data["YIELD"], errors="coerce")
    data["month"] = data["obs_date"].dt.strftime("%Y-%m")
    return (
        data.dropna(subset=["obs_date", "CURVECODE", "YEARSTOMATURITY", "YIELD"])
        .sort_values(["obs_date", "CURVECODE", "YEARSTOMATURITY"])
        .reset_index(drop=True)
    )


def build_result(curves: pd.DataFrame) -> pd.DataFrame:
    if curves.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    needed_codes = [GOV_CURVE_CODE, CDB_CURVE_CODE, LOCAL_GOV_CURVE_CODE]
    subset = curves[curves["CURVECODE"].astype(int).isin(needed_codes)].copy()
    subset["curve_label"] = subset["CURVECODE"].astype(int).map(CURVE_LABELS)

    rows: list[dict[str, object]] = []
    for tenor in TARGET_TENORS:
        tenor_data = subset[subset["YEARSTOMATURITY"].eq(tenor)]
        if tenor_data.empty:
            continue

        pivot = tenor_data.pivot_table(
            index=["month", "obs_date"],
            columns="curve_label",
            values="YIELD",
            aggfunc="last",
        )
        required_cols = ["国债", "国开债", "地方债"]
        if not set(required_cols).issubset(pivot.columns):
            continue

        pivot = pivot.dropna(subset=required_cols).reset_index().sort_values(["month", "obs_date"])

        for month, monthly in pivot.groupby("month", sort=True):
            window = monthly.tail(WINDOW_DAYS)
            if len(window) < WINDOW_DAYS:
                continue

            start = window.iloc[0]
            end = window.iloc[-1]

            local_gov_start = (float(start["地方债"]) - float(start["国债"])) * 10000
            local_gov_end = (float(end["地方债"]) - float(end["国债"])) * 10000
            local_cdb_start = (float(start["地方债"]) - float(start["国开债"])) * 10000
            local_cdb_end = (float(end["地方债"]) - float(end["国开债"])) * 10000

            rows.append(
                {
                    "月份": month,
                    "期限": f"{int(tenor)}Y",
                    "tenor_sort": tenor,
                    "地方债-国债期初利差(bp)": local_gov_start,
                    "地方债-国债期末利差(bp)": local_gov_end,
                    "地方债-国债利差变化(bp)": local_gov_end - local_gov_start,
                    "地方债-国开债期初利差(bp)": local_cdb_start,
                    "地方债-国开债期末利差(bp)": local_cdb_end,
                    "地方债-国开债利差变化(bp)": local_cdb_end - local_cdb_start,
                }
            )

    if not rows:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    result = pd.DataFrame(rows).sort_values(["月份", "tenor_sort"]).reset_index(drop=True)
    return result.drop(columns=["tenor_sort"])


def format_result(result: pd.DataFrame) -> pd.DataFrame:
    if result.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    formatted = result.copy()
    numeric_cols = [col for col in RESULT_COLUMNS if col not in {"月份", "期限"}]
    for col in numeric_cols:
        formatted[col] = pd.to_numeric(formatted[col], errors="coerce").map(
            lambda value: f"{value:.2f}" if pd.notna(value) else ""
        )
    return formatted[RESULT_COLUMNS]


def main() -> None:
    args = parse_args()
    curves = load_curve_points()
    result = format_result(build_result(curves))
    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
