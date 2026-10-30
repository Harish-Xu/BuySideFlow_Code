#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import matplotlib

    matplotlib.use("Agg")
except Exception:
    pass

import matplotlib.pyplot as plt

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


SCRIPT_DIR = Path(__file__).resolve().parent
AS_OF_DATE = "2025-06-06"
FUND_CODES = ("006567", "000574", "160133", "000628")
ROLLING_WINDOW = 63
FULL_TIMESERIES_CSV = "rolling_metrics_63d_timeseries.csv"
SUMMARY_METRIC_COLUMNS = [
    "共同区间末日63日累计收益",
    "共同区间末日63日最大回撤",
    "区间63日累计收益均值",
    "区间63日累计收益最大值",
    "区间63日累计收益最小值",
    "区间63日最大回撤均值",
    "区间63日最大回撤最小值",
]

plt.rcParams["font.sans-serif"] = ["Hiragino Sans GB", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def fetch_trading_days() -> pd.DatetimeIndex:
    recent_start = (pd.Timestamp(AS_OF_DATE) - pd.DateOffset(months=6)).strftime("%Y-%m-%d")
    sql = f"""
    WITH prior_days AS (
        SELECT DISTINCT DATE(TRADINGDATE) AS trading_day
        FROM qt_tradingdaynew
        WHERE SECUMARKET IN (83, 90) AND IFTRADINGDAY = 1
          AND TRADINGDATE < '{recent_start}'
        ORDER BY trading_day DESC
        LIMIT {ROLLING_WINDOW}
    )
    SELECT trading_day FROM prior_days
    UNION
    SELECT DATE(TRADINGDATE) AS trading_day
    FROM qt_tradingdaynew
    WHERE SECUMARKET IN (83, 90) AND IFTRADINGDAY = 1
      AND TRADINGDATE >= '{recent_start}'
      AND TRADINGDATE < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY)
    ORDER BY trading_day
    """
    days = query_to_dataframe(sql, max_rows=0, timeout=300)
    if days.empty:
        raise ValueError("交易日历为空")
    return pd.DatetimeIndex(pd.to_datetime(days["trading_day"]))


def fetch_nav(trading_days: pd.DatetimeIndex) -> pd.DataFrame:
    code_list = ", ".join(f"'{code}'" for code in FUND_CODES)
    sql = f"""
    WITH params AS (
        SELECT DATE('{AS_OF_DATE}') AS as_of_date
    ),
    target_funds AS (
        SELECT sm.INNERCODE, sm.SECUCODE, sm.SECUABBR
        FROM secumain sm
        WHERE sm.SECUCODE IN ({code_list})
          AND sm.SECUCATEGORY = 8
    )
    , nav_ranked AS (
        SELECT
            tf.SECUCODE AS fund_code,
            tf.SECUABBR AS fund_name,
            DATE(nv.TRADINGDAY) AS trading_day,
            nv.UNITNVRESTORED AS restored_unit_nav,
            ROW_NUMBER() OVER (
                PARTITION BY tf.INNERCODE, DATE(nv.TRADINGDAY)
                ORDER BY nv.INSERTTIME DESC, nv.ID DESC
            ) AS rn
        FROM params p
        JOIN target_funds tf
          ON 1 = 1
        JOIN mf_fundnetvalueretrans nv
          ON nv.INNERCODE = tf.INNERCODE
        WHERE nv.TRADINGDAY >= '{trading_days.min():%Y-%m-%d}'
          AND nv.TRADINGDAY < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
          AND nv.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    )
    SELECT fund_code, fund_name, trading_day, restored_unit_nav
    FROM nav_ranked
    WHERE rn = 1
    ORDER BY fund_code, trading_day
    """
    nav_df = query_to_dataframe(sql, max_rows=0, timeout=300)
    if nav_df.empty:
        raise ValueError("净值数据为空")
    nav_df["trading_day"] = pd.to_datetime(nav_df["trading_day"])
    nav_df["restored_unit_nav"] = pd.to_numeric(nav_df["restored_unit_nav"], errors="coerce")
    return nav_df.sort_values(["fund_code", "trading_day"]).reset_index(drop=True)


def calc_rolling_metrics(fund_nav_df: pd.DataFrame, trading_days: pd.DatetimeIndex) -> pd.DataFrame:
    fund_code = fund_nav_df["fund_code"].iloc[0]
    fund_name = fund_nav_df["fund_name"].iloc[0]
    nav_array = fund_nav_df.set_index("trading_day")["restored_unit_nav"].reindex(trading_days).to_numpy(dtype=float)
    n_obs = len(nav_array)

    rolling_return = np.full(n_obs, np.nan)
    rolling_drawdown = np.full(n_obs, np.nan)
    # A 63-return window includes its baseline NAV: 64 market-day prices.
    for end_idx in range(ROLLING_WINDOW, n_obs):
        window_nav = nav_array[end_idx - ROLLING_WINDOW : end_idx + 1]
        if not np.isfinite(window_nav).all() or (window_nav <= 0).any():
            continue
        rolling_return[end_idx] = window_nav[-1] / window_nav[0] - 1.0
        running_peak = np.maximum.accumulate(window_nav)
        rolling_drawdown[end_idx] = (window_nav / running_peak - 1.0).min()

    return pd.DataFrame(
        {
            "trading_day": trading_days,
            "fund_code": fund_code,
            "fund_name": fund_name,
            "rolling_return_63d": rolling_return,
            "rolling_max_drawdown_63d": rolling_drawdown,
        }
    )


def build_common_recent_metrics(nav_df: pd.DataFrame, trading_days: pd.DatetimeIndex) -> pd.DataFrame:
    metric_df = pd.concat(
        [calc_rolling_metrics(group, trading_days) for _, group in nav_df.groupby("fund_code", sort=False)],
        ignore_index=True,
    )
    metric_df = metric_df.dropna(subset=["rolling_return_63d", "rolling_max_drawdown_63d"]).copy()

    as_of_day = pd.Timestamp(AS_OF_DATE)
    recent_start = as_of_day - pd.DateOffset(months=6)
    recent_df = metric_df[(metric_df["trading_day"] >= recent_start) & (metric_df["trading_day"] <= as_of_day)].copy()

    fund_count = len(FUND_CODES)
    common_days = (
        recent_df.groupby("trading_day")["fund_code"]
        .nunique()
        .loc[lambda s: s == fund_count]
        .index
    )
    aligned_df = recent_df[recent_df["trading_day"].isin(common_days)].copy()
    if aligned_df.empty:
        raise ValueError("最近6个月内没有四只基金共同可比的滚动指标。")
    return aligned_df.sort_values(["trading_day", "fund_code"]).reset_index(drop=True)


def build_fund_summary(aligned_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for fund_code, fund_df in aligned_df.groupby("fund_code", sort=False):
        latest_idx = fund_df["trading_day"].idxmax()
        rows.append(
            {
                "基金代码": fund_code,
                "基金简称": fund_df["fund_name"].iloc[0],
                "共同区间末日63日累计收益": fund_df.loc[latest_idx, "rolling_return_63d"],
                "共同区间末日63日最大回撤": fund_df.loc[latest_idx, "rolling_max_drawdown_63d"],
                "区间63日累计收益均值": fund_df["rolling_return_63d"].mean(),
                "区间63日累计收益最大值": fund_df["rolling_return_63d"].max(),
                "区间63日累计收益最小值": fund_df["rolling_return_63d"].min(),
                "区间63日最大回撤均值": fund_df["rolling_max_drawdown_63d"].mean(),
                "区间63日最大回撤最小值": fund_df["rolling_max_drawdown_63d"].min(),
            }
        )

    summary_df = pd.DataFrame(rows).sort_values(
        ["共同区间末日63日累计收益", "基金代码"], ascending=[False, True]
    )
    numeric_cols = summary_df.select_dtypes(include=["number"]).columns
    summary_df[numeric_cols] = summary_df[numeric_cols].round(4)
    return summary_df.reset_index(drop=True)


def write_timeseries(aligned_df: pd.DataFrame) -> None:
    output_df = aligned_df.copy()
    output_df["trading_day"] = output_df["trading_day"].dt.strftime("%Y-%m-%d")
    output_df[["rolling_return_63d", "rolling_max_drawdown_63d"]] = output_df[
        ["rolling_return_63d", "rolling_max_drawdown_63d"]
    ].round(6)
    output_df.to_csv(SCRIPT_DIR / FULL_TIMESERIES_CSV, index=False, encoding="utf-8-sig")


def plot_metrics(aligned_df: pd.DataFrame) -> None:
    return_pivot = aligned_df.pivot(index="trading_day", columns="fund_code", values="rolling_return_63d") * 100
    drawdown_pivot = aligned_df.pivot(index="trading_day", columns="fund_code", values="rolling_max_drawdown_63d") * 100

    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    start_day = aligned_df["trading_day"].min()
    end_day = aligned_df["trading_day"].max()
    fig.suptitle(f"共同区间：{start_day:%Y-%m-%d} 至 {end_day:%Y-%m-%d}，"
                 f"有效交易日数：{aligned_df['trading_day'].nunique()}")
    for fund_code in return_pivot.columns:
        axes[0].plot(return_pivot.index, return_pivot[fund_code], label=fund_code, linewidth=1.2)
    axes[0].set_title("63日滚动累计收益对比")
    axes[0].set_ylabel("累计收益率(%)")
    axes[0].grid(True, alpha=0.3)
    axes[0].axhline(0, color="black", linewidth=0.6, linestyle="--")
    axes[0].legend(loc="best")

    for fund_code in drawdown_pivot.columns:
        axes[1].plot(drawdown_pivot.index, drawdown_pivot[fund_code], label=fund_code, linewidth=1.2)
    axes[1].set_title("63日滚动最大回撤对比")
    axes[1].set_ylabel("最大回撤(%)")
    axes[1].grid(True, alpha=0.3)
    axes[1].axhline(0, color="black", linewidth=0.6, linestyle="--")
    axes[1].legend(loc="best")

    plt.xticks(rotation=45)
    plt.tight_layout()
    fig.savefig(SCRIPT_DIR / "picture.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> pd.DataFrame:
    trading_days = fetch_trading_days()
    nav_df = fetch_nav(trading_days)
    aligned_df = build_common_recent_metrics(nav_df, trading_days)
    write_timeseries(aligned_df)
    plot_metrics(aligned_df)

    fund_summary = build_fund_summary(aligned_df)
    result_df = fund_summary.copy()
    for column in SUMMARY_METRIC_COLUMNS:
        result_df[column] = result_df[column].map(lambda x: "" if pd.isna(x) else f"{float(x):.4f}")
    result_df.to_csv(SCRIPT_DIR / "result.csv", index=False, encoding="utf-8-sig")

    print(fund_summary.to_string(index=False))
    return fund_summary


if __name__ == "__main__":
    main()
