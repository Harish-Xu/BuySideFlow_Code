#!/usr/bin/env python3
"""Reference implementation for bond_001.

Detect abnormal monthly volatility of the ChinaBond government 10Y-2Y spread
using each month's complete preceding rolling 20-month history.
"""

from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import get_db_connector


TARGET_START_MONTH = pd.Period("2025-01", freq="M")
AS_OF_DATE = pd.Timestamp("2025-04-30")
TARGET_END_MONTH = AS_OF_DATE.to_period("M")
HISTORY_MONTHS = 20
ANNUALIZATION_DAYS = 252
OUTPUT_COLUMNS = [
    "月份",
    "月度波动率",
    "异常阈值",
    "是否异常",
]


def render_sql(template: str, values: dict[str, str]) -> str:
    sql = template
    for key, value in values.items():
        sql = sql.replace("{{" + key + "}}", value)
    return sql


def query_curve_data(connector) -> pd.DataFrame:
    first_history_month = TARGET_START_MONTH - HISTORY_MONTHS
    # Include the preceding month so the first historical month's first
    # daily change has a predecessor (2023-04 for the 2023-05 window start).
    fetch_start_date = (first_history_month.start_time - pd.offsets.MonthBegin(1)).date().isoformat()
    end_date = AS_OF_DATE.date().isoformat()
    sql_template = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    sql = render_sql(
        sql_template,
        {
            "fetch_start_date": fetch_start_date,
            "end_date": end_date,
        },
    )
    df = connector.execute_sql_to_dataframe(sql, max_rows=0, timeout=180)
    if df.empty:
        raise RuntimeError("No ChinaBond government curve data returned")
    df["curve_date"] = pd.to_datetime(df["curve_date"])
    for col in ["yield_10y", "yield_2y"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["yield_10y", "yield_2y"]).sort_values("curve_date")
    return df.reset_index(drop=True)


def build_monthly_volatility(curve_df: pd.DataFrame) -> pd.DataFrame:
    df = curve_df.copy()
    df["curve_date"] = pd.to_datetime(df["curve_date"])
    df = df.sort_values("curve_date")
    if df["curve_date"].duplicated().any():
        raise ValueError("Duplicate curve dates")
    if df[["yield_10y", "yield_2y"]].isna().any().any():
        raise ValueError("Both curve tenors are required on each date")
    first_history_month = TARGET_START_MONTH - HISTORY_MONTHS
    if df.empty or df["curve_date"].min() >= first_history_month.start_time:
        raise ValueError("Missing predecessor for the first historical month")
    df["month"] = df["curve_date"].dt.to_period("M")
    # SQL normalizes percent yields to decimals; calculate spread changes in bp.
    df["spread_10y_2y"] = (df["yield_10y"] - df["yield_2y"]) * 10000.0
    df["delta_spread"] = df["spread_10y_2y"].diff()
    monthly = (
        df.dropna(subset=["delta_spread"])
        .groupby("month", as_index=False)
        .agg(
            observation_count=("delta_spread", "count"),
            annualized_volatility=(
                "delta_spread",
                lambda s: float(s.std(ddof=0) * np.sqrt(ANNUALIZATION_DAYS)),
            ),
        )
    )
    monthly = monthly[monthly["observation_count"] >= 2].copy()
    return monthly.sort_values("month").reset_index(drop=True)


def build_result(monthly_vol: pd.DataFrame) -> pd.DataFrame:
    by_month = monthly_vol.set_index("month")["annualized_volatility"]
    rows: list[dict[str, object]] = []
    for month in pd.period_range(TARGET_START_MONTH, TARGET_END_MONTH, freq="M"):
        if month not in by_month.index:
            raise RuntimeError(f"Missing monthly volatility for target month {month}")

        expected_months = pd.period_range(month - HISTORY_MONTHS, month - 1, freq="M")
        history = by_month.reindex(expected_months)
        if history.isna().any():
            missing = ", ".join(str(value) for value in history.index[history.isna()])
            raise ValueError(
                f"Month {month} requires {HISTORY_MONTHS} consecutive historical months; missing: {missing}"
            )

        current_vol = float(by_month.loc[month])
        median_vol = float(history.median())
        iqr = float(history.quantile(0.75, interpolation="linear")
                    - history.quantile(0.25, interpolation="linear"))
        threshold = median_vol + 1.5 * iqr
        rows.append(
            {
                "月份": str(month),
                "月度波动率": f"{current_vol:.4f}",
                "异常阈值": f"{threshold:.4f}",
                "是否异常": "是" if current_vol > threshold else "否",
            }
        )

    result = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    return result.sort_values("月份", ascending=False).reset_index(drop=True)


def main() -> pd.DataFrame:
    output_path = Path(__file__).with_name("result.csv")
    connector = get_db_connector()
    curve_df = query_curve_data(connector)
    monthly_vol = build_monthly_volatility(curve_df)
    result = build_result(monthly_vol)
    result.to_csv(output_path, index=False, encoding="utf-8")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
