#!/usr/bin/env python3
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


SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = SCRIPT_DIR / "result.csv"

YEAR = 2024
ANNUALIZATION_DAYS = 252
LOOKBACK_RETURNS = 60
LOOKBACK_PRICES = LOOKBACK_RETURNS + 1
PURE_BOND_THIRD_CODES = {130101, 130102, 130103}
MIN_COMBINED_SCALE = 500_000_000


def fetch_trading_calendar() -> pd.DataFrame:
    sql = """
    SELECT DATE(TRADINGDATE) AS trading_day
    FROM qt_tradingdaynew
    WHERE SECUMARKET = 83
      AND IFTRADINGDAY = 1
      AND TRADINGDATE BETWEEN DATE('2023-01-01') AND DATE('2024-12-31')
    ORDER BY TRADINGDATE
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=300)
    if df is None or df.empty:
        raise ValueError("未取得 2023-2024 沪市交易日历。")
    df = df.copy()
    df["trading_day"] = pd.to_datetime(df["trading_day"])
    return df


def build_month_schedule(calendar: pd.DataFrame) -> pd.DataFrame:
    cal = calendar.copy()
    cal["month"] = cal["trading_day"].dt.to_period("M")
    months_2024 = cal[cal["trading_day"].dt.year == YEAR]
    first_days = months_2024.groupby("month", as_index=False)["trading_day"].min()
    last_days = months_2024.groupby("month", as_index=False)["trading_day"].max()
    schedule = first_days.merge(last_days, on="month", suffixes=("_first", "_last"))
    schedule = schedule.rename(
        columns={"trading_day_first": "rebalance_date", "trading_day_last": "month_end"}
    )
    all_days = cal["trading_day"].tolist()
    prev_days = []
    screen_windows = []
    for rebalance_date in schedule["rebalance_date"]:
        before = [day for day in all_days if day < rebalance_date]
        if len(before) < LOOKBACK_PRICES:
            raise ValueError(f"{rebalance_date:%Y-%m-%d} 之前不足 {LOOKBACK_PRICES} 个交易日。")
        prev_days.append(before[-1])
        screen_windows.append(before[-LOOKBACK_PRICES:])
    schedule["classification_asof"] = prev_days
    schedule["screen_window"] = screen_windows
    return schedule


def chunks(values: list[int], size: int) -> list[list[int]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def fetch_fund_archives() -> pd.DataFrame:
    sql = """
    SELECT
        f.INNERCODE,
        f.SECURITYCODE AS fund_code,
        f.SECURITYCODE AS fund_name,
        DATE(f.ESTABLISHMENTDATE) AS establishment_date,
        DATE(f.EXPIREDATE) AS expire_date
    FROM mf_fundarchives f
    WHERE COALESCE(NULLIF(f.MAINCODE, ''), f.SECURITYCODE) = f.SECURITYCODE
      AND f.TYPE = 2
      AND COALESCE(f.IFFOF, 2) <> 1
      AND DATE(f.ESTABLISHMENTDATE) <= DATE('2024-12-31')
      AND (f.EXPIREDATE IS NULL OR DATE(f.EXPIREDATE) > DATE('2023-12-29'))
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=300)
    if df is None or df.empty:
        raise ValueError("未取得开放式主份额基金档案。")
    df = df.copy()
    df["INNERCODE"] = pd.to_numeric(df["INNERCODE"], errors="coerce").astype("Int64")
    df["establishment_date"] = pd.to_datetime(df["establishment_date"], errors="coerce")
    df["expire_date"] = pd.to_datetime(df["expire_date"], errors="coerce")
    return df.dropna(subset=["INNERCODE"])


def fetch_type_rows() -> pd.DataFrame:
    sql = """
    SELECT
        t.INNERCODE,
        t.SECASSETCATCODE,
        t.THIRDASSETCATCODE,
        DATE(t.EFFECTIVEDATE) AS effective_date,
        DATE(t.CANCELDATE) AS cancel_date,
        t.INSERTTIME,
        t.ID
    FROM mf_jyfundtype t
    JOIN mf_fundarchives f
      ON f.INNERCODE = t.INNERCODE
    WHERE COALESCE(NULLIF(f.MAINCODE, ''), f.SECURITYCODE) = f.SECURITYCODE
      AND f.TYPE = 2
      AND COALESCE(f.IFFOF, 2) <> 1
      AND t.STANDARD = 75
      AND t.EFFECTIVEDATE <= DATE('2024-12-30')
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > DATE('2023-12-29'))
      AND t.INSERTTIME < DATE('2025-01-01')
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        raise ValueError("未取得聚源分类历史记录。")
    df = df.copy()
    df["INNERCODE"] = pd.to_numeric(df["INNERCODE"], errors="coerce").astype("Int64")
    df["SECASSETCATCODE"] = pd.to_numeric(df["SECASSETCATCODE"], errors="coerce")
    df["THIRDASSETCATCODE"] = pd.to_numeric(df["THIRDASSETCATCODE"], errors="coerce")
    df["effective_date"] = pd.to_datetime(df["effective_date"], errors="coerce")
    df["cancel_date"] = pd.to_datetime(df["cancel_date"], errors="coerce")
    df["INSERTTIME"] = pd.to_datetime(df["INSERTTIME"], errors="coerce")
    df["ID"] = pd.to_numeric(df["ID"], errors="coerce")
    return df.dropna(subset=["INNERCODE", "effective_date"])


def fetch_scale_rows() -> pd.DataFrame:
    sql = """
    SELECT
        s.INNERCODE,
        DATE(s.ENDDATE) AS scale_date,
        s.COMBNVI AS combined_scale,
        s.INSERTTIME,
        s.UPDATETIME,
        s.ID
    FROM mf_scaleanalysis s
    JOIN mf_fundarchives f
      ON f.INNERCODE = s.INNERCODE
    WHERE COALESCE(NULLIF(f.MAINCODE, ''), f.SECURITYCODE) = f.SECURITYCODE
      AND f.TYPE = 2
      AND COALESCE(f.IFFOF, 2) <> 1
      AND s.ENDDATE <= DATE('2024-12-30')
      AND s.COMBNVI IS NOT NULL
      AND s.COMBNVI > 0
      AND s.INSERTTIME < DATE('2025-01-01')
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        raise ValueError("未取得合并规模历史记录。")
    df = df.copy()
    df["INNERCODE"] = pd.to_numeric(df["INNERCODE"], errors="coerce").astype("Int64")
    df["scale_date"] = pd.to_datetime(df["scale_date"], errors="coerce")
    df["combined_scale"] = pd.to_numeric(df["combined_scale"], errors="coerce")
    df["INSERTTIME"] = pd.to_datetime(df["INSERTTIME"], errors="coerce")
    df["UPDATETIME"] = pd.to_datetime(df["UPDATETIME"], errors="coerce")
    df["ID"] = pd.to_numeric(df["ID"], errors="coerce")
    return df.dropna(subset=["INNERCODE", "scale_date", "combined_scale"])


def fetch_nav_rows(nav_dates: list[pd.Timestamp], innercodes: list[int]) -> pd.DataFrame:
    if not innercodes:
        raise ValueError("纯债候选基金池为空，无法取净值。")
    start_date = min(nav_dates).strftime("%Y-%m-%d")
    end_date = max(nav_dates).strftime("%Y-%m-%d")
    frames: list[pd.DataFrame] = []
    innercode_chunks = chunks(sorted(set(innercodes)), 120)
    for innercode_chunk in innercode_chunks:
        code_filter = ", ".join(str(code) for code in innercode_chunk)
        sql = f"""
    SELECT
        n.INNERCODE,
        DATE(n.TRADINGDAY) AS trading_day,
        n.UNITNVRESTORED AS restored_nav,
        n.INSERTTIME,
        n.UPDATETIME,
        n.JSID
    FROM mf_fundnetvalueretrans n
    WHERE n.INNERCODE IN ({code_filter})
      AND n.TRADINGDAY BETWEEN DATE('{start_date}') AND DATE('{end_date}')
      AND n.UNITNVRESTORED IS NOT NULL
      AND n.UNITNVRESTORED > 0
        """
        chunk_df = query_to_dataframe(sql, max_rows=0, timeout=300)
        if chunk_df is not None and not chunk_df.empty:
            frames.append(chunk_df)
    if not frames:
        raise ValueError("未取得复权净值面板。")
    df = pd.concat(frames, ignore_index=True)
    df = df.copy()
    df["INNERCODE"] = pd.to_numeric(df["INNERCODE"], errors="coerce").astype("Int64")
    df["trading_day"] = pd.to_datetime(df["trading_day"], errors="coerce")
    df["restored_nav"] = pd.to_numeric(df["restored_nav"], errors="coerce")
    df["INSERTTIME"] = pd.to_datetime(df["INSERTTIME"], errors="coerce")
    df["UPDATETIME"] = pd.to_datetime(df["UPDATETIME"], errors="coerce")
    df["JSID"] = pd.to_numeric(df["JSID"], errors="coerce")
    return df.dropna(subset=["INNERCODE", "trading_day", "restored_nav"])


def deduplicate_nav(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    sort_cols = ["INNERCODE", "trading_day", "UPDATETIME", "INSERTTIME", "JSID"]
    return (
        df.sort_values(sort_cols)
        .drop_duplicates(["INNERCODE", "trading_day"], keep="last")
        .reset_index(drop=True)
    )


def fund_pool_asof(
    archive_rows: pd.DataFrame,
    type_rows: pd.DataFrame,
    scale_rows: pd.DataFrame,
    classification_asof: pd.Timestamp,
    rebalance_date: pd.Timestamp,
) -> pd.DataFrame:
    establishment_cutoff = classification_asof - pd.DateOffset(years=1)
    archive = archive_rows[
        (archive_rows["establishment_date"] <= establishment_cutoff)
        & (
            archive_rows["expire_date"].isna()
            | (archive_rows["expire_date"] > classification_asof)
        )
    ]
    typed = type_rows[
        (type_rows["effective_date"] <= classification_asof)
        & (type_rows["INSERTTIME"] < rebalance_date)
        & (type_rows["cancel_date"].isna() | (type_rows["cancel_date"] > classification_asof))
    ].copy()
    if typed.empty or archive.empty:
        return pd.DataFrame(columns=archive_rows.columns)

    typed = (
        typed.sort_values(["INNERCODE", "effective_date", "INSERTTIME", "ID"])
        .drop_duplicates("INNERCODE", keep="last")
    )
    pure_innercodes = set(
        typed.loc[typed["THIRDASSETCATCODE"].isin(PURE_BOND_THIRD_CODES), "INNERCODE"]
        .dropna()
        .astype(int)
        .tolist()
    )
    scale = scale_rows[
        (scale_rows["scale_date"] <= classification_asof)
        & (scale_rows["INSERTTIME"] < rebalance_date)
        & (scale_rows["UPDATETIME"] < rebalance_date)
    ].copy()
    if scale.empty:
        return pd.DataFrame(columns=archive_rows.columns)
    scale = (
        scale.sort_values(["INNERCODE", "scale_date", "INSERTTIME", "UPDATETIME", "ID"])
        .drop_duplicates("INNERCODE", keep="last")
    )
    scale_innercodes = set(
        scale.loc[scale["combined_scale"] >= MIN_COMBINED_SCALE, "INNERCODE"]
        .dropna()
        .astype(int)
        .tolist()
    )
    eligible_innercodes = pure_innercodes & scale_innercodes
    return archive[archive["INNERCODE"].astype(int).isin(eligible_innercodes)].copy()


def screen_month(
    row: pd.Series,
    archive_rows: pd.DataFrame,
    type_rows: pd.DataFrame,
    scale_rows: pd.DataFrame,
    nav_rows: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, object]]:
    rebalance_date = pd.Timestamp(row["rebalance_date"])
    month_end = pd.Timestamp(row["month_end"])
    classification_asof = pd.Timestamp(row["classification_asof"])
    screen_dates = list(row["screen_window"])

    fund_pool = fund_pool_asof(
        archive_rows, type_rows, scale_rows, classification_asof, rebalance_date
    )
    pool_codes = set(fund_pool["INNERCODE"].dropna().astype(int).tolist())
    if not pool_codes:
        return pd.DataFrame(), month_stats(rebalance_date, 0, 0, 0, 0, 0.0)

    selection_rows = nav_rows[
        nav_rows["INNERCODE"].astype(int).isin(pool_codes)
        & nav_rows["trading_day"].isin(screen_dates)
        & (nav_rows["INSERTTIME"] < rebalance_date)
        & (nav_rows["UPDATETIME"] < rebalance_date)
    ]
    selection_rows = deduplicate_nav(selection_rows)
    price_matrix = (
        selection_rows.pivot(index="trading_day", columns="INNERCODE", values="restored_nav")
        .reindex(screen_dates)
        .sort_index()
    )
    complete = price_matrix.columns[price_matrix.notna().sum() == LOOKBACK_PRICES].tolist()
    if not complete:
        return pd.DataFrame(), month_stats(rebalance_date, 0, 0, 0, 0, 0.0)

    complete_prices = price_matrix[complete]
    returns = complete_prices.pct_change().dropna(how="all")
    total_return = complete_prices.iloc[-1] / complete_prices.iloc[0] - 1.0
    annual_vol = returns.std(ddof=0) * math.sqrt(ANNUALIZATION_DAYS)
    metrics = pd.DataFrame(
        {
            "INNERCODE": complete,
            "lookback_return": total_return.reindex(complete).to_numpy(dtype=float),
            "annual_volatility": annual_vol.reindex(complete).to_numpy(dtype=float),
        }
    )
    metrics = metrics[
        (metrics["lookback_return"] > 0)
        & metrics["annual_volatility"].notna()
        & np.isfinite(metrics["annual_volatility"])
    ].copy()
    if metrics.empty:
        return pd.DataFrame(), month_stats(rebalance_date, len(complete), 0, 0, 0, 0.0)

    name_map = fund_pool[["INNERCODE", "fund_code", "fund_name"]].set_index("INNERCODE")
    metrics = metrics.join(name_map, on="INNERCODE")
    metrics = metrics.sort_values(["annual_volatility", "fund_code"], kind="mergesort")
    selected_n = math.ceil(len(metrics) * 0.05)
    selected = metrics.head(selected_n).copy()

    holding_rows = deduplicate_nav(
        nav_rows[
            nav_rows["INNERCODE"].astype(int).isin(selected["INNERCODE"].astype(int))
            & (nav_rows["trading_day"] >= rebalance_date)
            & (nav_rows["trading_day"] <= month_end)
        ]
    )
    entry_rows = holding_rows[holding_rows["trading_day"] == rebalance_date][
        ["INNERCODE", "restored_nav"]
    ].rename(columns={"restored_nav": "entry_nav"})
    successful_codes = set(entry_rows["INNERCODE"].dropna().astype(int).tolist())
    successful_rows = holding_rows[
        holding_rows["INNERCODE"].astype(int).isin(successful_codes)
    ]
    exit_rows = (
        successful_rows.sort_values(["INNERCODE", "trading_day"])
        .drop_duplicates("INNERCODE", keep="last")[["INNERCODE", "restored_nav"]]
        .rename(columns={"restored_nav": "exit_nav"})
    )
    holding_returns = entry_rows.merge(exit_rows, on="INNERCODE", how="inner")
    holding_returns["holding_return"] = (
        holding_returns["exit_nav"] / holding_returns["entry_nav"] - 1.0
    )
    monthly_returns = holding_returns.set_index("INNERCODE")["holding_return"]
    monthly_return = float(monthly_returns.mean()) if not monthly_returns.empty else 0.0

    selected["rebalance_date"] = f"{rebalance_date:%Y-%m-%d}"
    selected["month_end"] = f"{month_end:%Y-%m-%d}"
    selected["holding_return"] = selected["INNERCODE"].map(monthly_returns)
    selected["monthly_return"] = monthly_return

    stats = month_stats(
        rebalance_date,
        len(complete),
        len(metrics),
        len(selected),
        int(monthly_returns.count()),
        monthly_return,
    )
    stats["fund_pool_count"] = int(len(fund_pool))
    return selected.reset_index(drop=True), stats


def month_stats(
    rebalance_date: pd.Timestamp,
    complete_count: int,
    positive_count: int,
    selected_count: int,
    return_count: int,
    monthly_return: float,
) -> dict[str, object]:
    return {
        "month": f"{rebalance_date:%Y-%m}",
        "complete_count": int(complete_count),
        "positive_count": int(positive_count),
        "selected_count": int(selected_count),
        "return_count": int(return_count),
        "monthly_return": monthly_return,
    }


def build_backtest() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    calendar = fetch_trading_calendar()
    schedule = build_month_schedule(calendar)
    nav_dates = sorted(
        {
            pd.Timestamp(day)
            for _, row in schedule.iterrows()
            for day in list(row["screen_window"])
            + [pd.Timestamp(row["rebalance_date"]), pd.Timestamp(row["month_end"])]
        }
    )
    archive_rows = fetch_fund_archives()
    type_rows = fetch_type_rows()
    scale_rows = fetch_scale_rows()
    union_pool: set[int] = set()
    for _, row in schedule.iterrows():
        month_pool = fund_pool_asof(
            archive_rows,
            type_rows,
            scale_rows,
            pd.Timestamp(row["classification_asof"]),
            pd.Timestamp(row["rebalance_date"]),
        )
        union_pool.update(month_pool["INNERCODE"].dropna().astype(int).tolist())
    nav_rows = fetch_nav_rows(nav_dates, sorted(union_pool))

    monthly_stats: list[dict[str, object]] = []
    selected_rows: list[pd.DataFrame] = []
    for _, row in schedule.iterrows():
        selected, stats = screen_month(row, archive_rows, type_rows, scale_rows, nav_rows)
        monthly_stats.append(stats)
        if not selected.empty:
            selected_rows.append(selected)

    monthly = pd.DataFrame(monthly_stats)
    monthly["portfolio_nav"] = (1.0 + monthly["monthly_return"]).cumprod()
    nav_path = pd.concat(
        [
            pd.DataFrame({"month": ["2023-12"], "portfolio_nav": [1.0]}),
            monthly[["month", "portfolio_nav"]],
        ],
        ignore_index=True,
    )
    monthly["cumulative_return"] = monthly["portfolio_nav"] - 1.0
    nav_path["drawdown"] = (
        nav_path["portfolio_nav"] / nav_path["portfolio_nav"].cummax() - 1.0
    )
    monthly["cumulative_max_drawdown"] = (
        nav_path["drawdown"].expanding().min().iloc[1:].to_numpy()
    )
    monthly["cumulative_win_rate"] = (
        (monthly["monthly_return"] > 0).expanding().mean()
    )
    result = pd.DataFrame(
        {
            "调仓月份": monthly["month"],
            "实际持仓基金数": monthly["return_count"].astype(int),
            "月度组合收益": monthly["monthly_return"].map(lambda x: f"{x * 100:.2f}"),
            "累计收益": monthly["cumulative_return"].map(lambda x: f"{x * 100:.2f}"),
            "累计最大回撤": monthly["cumulative_max_drawdown"].map(
                lambda x: f"{x * 100:.2f}"
            ),
            "累计月度胜率": monthly["cumulative_win_rate"].map(
                lambda x: f"{x * 100:.2f}"
            ),
        }
    )
    selected_detail = (
        pd.concat(selected_rows, ignore_index=True) if selected_rows else pd.DataFrame()
    )
    return result, monthly, selected_detail


def main() -> pd.DataFrame:
    result, monthly, selected_detail = build_backtest()
    result.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
