#!/usr/bin/env python3
"""stock_033: 低波放量信号复盘分析

题面: 截至 2025-08-20, 复盘沪深300和中证500 2025 年以来
低波放量信号后的表现. 输出按指数和持有期汇总的统计.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe

AS_OF_DATE = pd.Timestamp("2025-08-20")
ANNUALIZATION_DAYS = 252
VOL_WINDOW = 30
VOL_PERCENTILE_WINDOW = 252
TURNOVER_RECENT_DAYS = 5
TURNOVER_PREV_DAYS = 5
SCAN_START_DATE = pd.Timestamp("2025-01-01")
RET_HORIZONS = {5: "5日", 20: "20日"}


def load_quotes() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    sql = sql_path.read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=300)
    if df is None or df.empty:
        raise ValueError("No index quote data returned")
    df["TRADINGDAY"] = pd.to_datetime(df["TRADINGDAY"]).dt.normalize()
    for col in ["CLOSEPRICE", "TURNOVERVALUE", "sort_order"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.loc[df["CLOSEPRICE"] <= 0, "CLOSEPRICE"] = np.nan
    df = df.sort_values(
        ["SECUCODE", "TRADINGDAY"]
    ).reset_index(drop=True)
    return df


def daily_features(grp: pd.DataFrame) -> pd.DataFrame:
    """对单指数日行情计算 vol、分位、成交额、信号."""
    grp = grp.sort_values("TRADINGDAY").copy().reset_index(drop=True)
    grp["daily_return"] = grp["CLOSEPRICE"].pct_change(fill_method=None)
    grp["vol_30"] = (
        grp["daily_return"]
        .rolling(VOL_WINDOW, min_periods=VOL_WINDOW)
        .std(ddof=0)
        * math.sqrt(ANNUALIZATION_DAYS)
        * 100.0
    )
    # 成交额: 两个不重叠窗口
    # 近5日: T-4 ~ T; 此前5日: T-9 ~ T-5 (shift 5 天后取 rolling)
    grp["turnover_recent"] = (
        grp["TURNOVERVALUE"]
        .rolling(TURNOVER_RECENT_DAYS, min_periods=TURNOVER_RECENT_DAYS)
        .mean()
    )
    grp["_turnover_prev_shifted"] = grp["TURNOVERVALUE"].shift(TURNOVER_RECENT_DAYS)
    grp["turnover_prev"] = (
        grp["_turnover_prev_shifted"]
        .rolling(TURNOVER_PREV_DAYS, min_periods=TURNOVER_PREV_DAYS)
        .mean()
    )
    grp["lift_pct"] = (
        (grp["turnover_recent"] / grp["turnover_prev"] - 1.0) * 100.0
    )
    vol_values = grp["vol_30"].to_numpy(dtype=float)
    vol_ranks = np.full(len(grp), np.nan)
    for idx in range(VOL_PERCENTILE_WINDOW, len(grp)):
        prior = vol_values[:idx]
        history = prior[np.isfinite(prior)][-VOL_PERCENTILE_WINDOW:]
        current = vol_values[idx]
        if np.isfinite(current) and len(history) == VOL_PERCENTILE_WINDOW:
            vol_ranks[idx] = float((history <= current).mean() * 100.0)
    grp["vol_rank"] = vol_ranks
    # 信号触发
    mask = (
        (grp["vol_rank"] <= 40.0)
        & (grp["lift_pct"] > 10.0)
        & grp["vol_rank"].notna()
        & grp["turnover_prev"].notna()
        & (grp["turnover_prev"] > 0)
    )
    grp["signal_raw"] = mask.astype(float)
    grp["is_new_signal"] = mask & (~mask.shift(1, fill_value=False).astype(bool))
    grp["signal"] = grp["is_new_signal"].astype(int)
    return grp


def compute_returns(grp: pd.DataFrame) -> pd.DataFrame:
    """对每个信号日计算 T+5 和 T+20 收益 (百分比)."""
    grp = grp.sort_values("TRADINGDAY").copy().reset_index(drop=True)
    grp["ret_5d"] = (grp["CLOSEPRICE"].shift(-5) / grp["CLOSEPRICE"] - 1.0) * 100.0
    grp["ret_20d"] = (grp["CLOSEPRICE"].shift(-20) / grp["CLOSEPRICE"] - 1.0) * 100.0
    return grp


def build_result(quotes: pd.DataFrame) -> pd.DataFrame:
    all_signals = []
    for secucode, grp_raw in quotes.groupby("SECUCODE", sort=False):
        grp = daily_features(grp_raw)
        grp = compute_returns(grp)
        idx_name = grp["index_name"].iloc[0]
        sort_order = int(grp["sort_order"].iloc[0])
        for _, row in grp.iterrows():
            if (
                int(row.get("signal", 0)) == 1
                and row["TRADINGDAY"] >= SCAN_START_DATE
                and row["TRADINGDAY"] <= AS_OF_DATE
            ):
                all_signals.append(
                    {
                        "sort_order": sort_order,
                        "指数": idx_name,
                        "信号日期": row["TRADINGDAY"],
                        "5日收益(%)": row["ret_5d"],
                        "20日收益(%)": row["ret_20d"],
                    }
                )

    if not all_signals:
        print("WARNING: No signals found in 2025 scan period")

    cols = ["sort_order", "指数", "信号日期", "5日收益(%)", "20日收益(%)"]
    signals_df = pd.DataFrame(all_signals, columns=cols)

    rows = []
    index_meta = (
        quotes[["sort_order", "指数"]]
        if "指数" in quotes.columns
        else quotes[["sort_order", "index_name"]].rename(columns={"index_name": "指数"})
    ).drop_duplicates().sort_values("sort_order")
    for _, meta in index_meta.iterrows():
        sort_order = int(meta["sort_order"])
        index_name = meta["指数"]
        idx_grp = signals_df[signals_df["指数"] == index_name]
        for horizon, horizon_label in RET_HORIZONS.items():
            col_ret = f"{horizon_label}收益(%)"
            valid = idx_grp[col_ret].dropna()
            if len(valid) == 0:
                rows.append(
                    {
                        "sort_order_idx": sort_order,
                        "sort_order_horizon": horizon,
                        "指数": index_name,
                        "持有期": horizon_label,
                        "信号次数": 0,
                        "平均收益(%)": "",
                        "中位数收益(%)": "",
                        "胜率(%)": "",
                    }
                )
            else:
                n = len(valid)
                avg_ret = round(float(valid.mean()), 2)
                med_ret = round(float(valid.median()), 2)
                win_rate = round(float((valid > 0).mean() * 100.0), 2)
                rows.append(
                    {
                        "sort_order_idx": sort_order,
                        "sort_order_horizon": horizon,
                        "指数": index_name,
                        "持有期": horizon_label,
                        "信号次数": n,
                        "平均收益(%)": avg_ret,
                        "中位数收益(%)": med_ret,
                        "胜率(%)": win_rate,
                    }
                )

    result = pd.DataFrame(
        rows,
        columns=[
            "sort_order_idx", "sort_order_horizon", "指数", "持有期",
            "信号次数", "平均收益(%)", "中位数收益(%)", "胜率(%)",
        ],
    )
    result = result.sort_values(
        ["sort_order_idx", "sort_order_horizon"]
    ).reset_index(drop=True)

    display_cols = [
        "指数", "持有期", "信号次数", "平均收益(%)", "中位数收益(%)", "胜率(%)",
    ]
    return result[display_cols]


def main() -> pd.DataFrame:
    output_path = Path(__file__).with_name("result.csv")
    quotes = load_quotes()
    result = build_result(quotes)
    result.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    main()
