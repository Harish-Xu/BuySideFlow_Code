#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe

DEFAULT_AS_OF_DATE = "2026-01-09"
ROLLING_WINDOW_MONTHS = 24
INDICATOR_CODE = 150006354
HISTORY_START = "2023-02-01"
RESULT_COLUMNS = ["月份", "社融同比(%)", "国债收益率(%)", "滚动相关系数"]


def fetch_social_financing_yoy(as_of_date: str) -> pd.DataFrame:
    sql = f"""
    SELECT DATE(ENDDATE) AS obs_date, INFOPUBLDATE AS publish_date,
           DATAVALUE AS social_financing_yoy, ID AS record_id
    FROM c_ed_macroindicatordata
    WHERE INDICATORCODE = {INDICATOR_CODE}
      AND ENDDATE >= DATE('{HISTORY_START}') AND ENDDATE < DATE('2026-01-01')
      AND INFOPUBLDATE < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
    ORDER BY ENDDATE, INFOPUBLDATE, ID
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=120)
    if df.empty:
        raise ValueError("No publicly available social-financing observations")
    df["obs_date"] = pd.to_datetime(df["obs_date"])
    df["month"] = df["obs_date"].dt.to_period("M").dt.to_timestamp("M")
    df["social_financing_yoy"] = pd.to_numeric(df["social_financing_yoy"], errors="coerce")
    # Select the latest available version before judging missingness.
    return df.drop_duplicates("month", keep="last")[["month", "social_financing_yoy"]]


def fetch_treasury_10y_monthly(as_of_date: str) -> pd.DataFrame:
    sql = f"""
    SELECT DATE(ENDDATE) AS trade_date, YIELD AS treasury_yield_10y, ID AS record_id
    FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE CURVECODE = 10 AND YIELDTYPECODE = 1 AND YEARSTOMATURITY = 10
      AND ENDDATE >= DATE('{HISTORY_START}') AND ENDDATE < DATE('2026-01-01')
      AND ENDDATE < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
    ORDER BY ENDDATE, ID
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=120)
    if df.empty:
        raise ValueError("No 10-year treasury observations")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["month"] = df["trade_date"].dt.to_period("M").dt.to_timestamp("M")
    df["treasury_yield_10y"] = pd.to_numeric(df["treasury_yield_10y"], errors="coerce") * 100
    return df.drop_duplicates("month", keep="last")[["month", "treasury_yield_10y"]]


def build_rolling_correlation(social_df: pd.DataFrame, treasury_df: pd.DataFrame) -> pd.DataFrame:
    months = pd.period_range(HISTORY_START, "2025-12", freq="M").to_timestamp("M")
    panel = pd.DataFrame({"month": months})
    panel = panel.merge(social_df, on="month", how="left", validate="one_to_one")
    panel = panel.merge(treasury_df, on="month", how="left", validate="one_to_one")
    panel["rolling_correlation_24m"] = np.nan
    for pos in range(ROLLING_WINDOW_MONTHS - 1, len(panel)):
        window = panel.iloc[pos - ROLLING_WINDOW_MONTHS + 1:pos + 1]
        x, y = window["social_financing_yoy"], window["treasury_yield_10y"]
        if x.notna().all() and y.notna().all() and x.nunique() > 1 and y.nunique() > 1:
            panel.loc[pos, "rolling_correlation_24m"] = x.corr(y)
    # Current availability controls display; a historical gap only blanks the correlation.
    return panel.loc[
        (panel["month"].dt.year == 2025)
        & panel["social_financing_yoy"].notna() & panel["treasury_yield_10y"].notna()
    ].reset_index(drop=True)


def format_result(panel: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "月份": panel["month"].dt.strftime("%Y-%m"),
        "社融同比(%)": panel["social_financing_yoy"].map(lambda x: f"{x:.2f}"),
        "国债收益率(%)": panel["treasury_yield_10y"].map(lambda x: f"{x:.2f}"),
        "滚动相关系数": panel["rolling_correlation_24m"].map(lambda x: f"{x:.4f}" if pd.notna(x) else ""),
    }, columns=RESULT_COLUMNS)


def save_chart(panel: pd.DataFrame, chart_path: str | Path) -> str:
    output_path = Path(chart_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(12, 5))
    x = np.arange(len(panel))
    values = panel["rolling_correlation_24m"].to_numpy(dtype=float)
    ax.bar(x, values, width=0.65, color="#277B85")
    ax.set_xticks(x, panel["month"].dt.strftime("%Y-%m"), rotation=40, ha="right")
    ax.set_ylim(-1.1, 1.1)
    ax.axhline(0, color="#666666", linewidth=0.8)
    ax.set_title("Social financing YoY vs. 10Y Treasury: trailing 24-month Pearson correlation")
    ax.set_xlabel("2025 observation month | available as of 2026-01-09")
    ax.set_ylabel("Correlation")
    ax.grid(axis="y", alpha=0.2)
    for pos, value in zip(x, values):
        ax.annotate(f"{value:.4f}" if np.isfinite(value) else "NA",
                    (pos, value if np.isfinite(value) else 0),
                    xytext=(0, 5 if not np.isfinite(value) or value >= 0 else -5),
                    textcoords="offset points", ha="center",
                    va="bottom" if not np.isfinite(value) or value >= 0 else "top", fontsize=9)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    return str(output_path)


def main() -> pd.DataFrame:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of-date", default=DEFAULT_AS_OF_DATE)
    parser.add_argument("--output-path", default=str(Path(__file__).with_name("result.csv")))
    parser.add_argument("--chart-path", default=str(Path(__file__).with_name("picture.png")))
    args = parser.parse_args()
    as_of_date = pd.Timestamp(args.as_of_date).strftime("%Y-%m-%d")
    panel = build_rolling_correlation(fetch_social_financing_yoy(as_of_date), fetch_treasury_10y_monthly(as_of_date))
    result = format_result(panel)
    result.to_csv(args.output_path, index=False, encoding="utf-8-sig")
    save_chart(panel, args.chart_path)
    return result


if __name__ == "__main__":
    main()
