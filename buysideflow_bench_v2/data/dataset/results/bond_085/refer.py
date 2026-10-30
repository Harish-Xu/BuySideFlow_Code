#!/usr/bin/env python3
from __future__ import annotations

import argparse
from decimal import Decimal
from statistics import median
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import get_connection


SIGNAL_START = pd.Timestamp("2024-01-01")
HISTORY_START = SIGNAL_START - pd.Timedelta(weeks=8)
LAST_SIGNAL_WEEK_START = pd.Timestamp("2024-12-30")
ROLLING_WEEKS = 8
STD_MULTIPLIER = Decimal("1.5")
BOND_TYPES = ["国债", "国开债"]
HORIZONS = [1, 5]

RESULT_COLUMNS = [
    "债券类型",
    "是否放量",
    "观察窗口",
    "样本数",
    "平均变化(bp)",
    "中位数变化(bp)",
    "上行占比(%)",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="2024 年国债、国开债供给冲击后 5Y 曲线反应")
    parser.add_argument("--output-path", default=str(Path(__file__).with_name("result.csv")))
    return parser.parse_args()


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def load_raw_data() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=180000")
            cursor.execute(sql)
            return pd.DataFrame(cursor.fetchall(), columns=[col[0] for col in cursor.description])
    finally:
        connection.close()


def decimal_value(value):
    return None if pd.isna(value) else Decimal(str(value))


def split_raw_data(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if raw.empty:
        return pd.DataFrame(), pd.DataFrame()

    issue = raw.loc[
        raw["row_type"] == "issue",
        ["bond_type", "week_start_date", "weekly_issue_size"],
    ].copy()
    issue["week_start_date"] = pd.to_datetime(issue["week_start_date"], errors="coerce")
    issue["weekly_issue_size"] = issue["weekly_issue_size"].map(decimal_value)
    issue = issue.dropna(subset=["bond_type", "week_start_date"])

    curve = raw.loc[
        raw["row_type"] == "curve",
        ["bond_type", "obs_date", "curve_yield"],
    ].copy()
    curve["obs_date"] = pd.to_datetime(curve["obs_date"], errors="coerce")
    curve["curve_yield"] = curve["curve_yield"].map(decimal_value)
    curve = curve.dropna(subset=["bond_type", "obs_date"])
    curve = curve.sort_values(["bond_type", "obs_date"]).reset_index(drop=True)

    return issue.reset_index(drop=True), curve


def build_weekly_panel(issue: pd.DataFrame) -> pd.DataFrame:
    weeks = pd.date_range(HISTORY_START, LAST_SIGNAL_WEEK_START, freq="W-MON")
    panel = pd.MultiIndex.from_product(
        [BOND_TYPES, weeks], names=["bond_type", "week_start_date"]
    ).to_frame(index=False)

    weekly_issue = (
        issue.groupby(["bond_type", "week_start_date"], as_index=False)["weekly_issue_size"].agg(
            lambda values: sum((decimal_value(v) for v in values), Decimal(0)) if values.notna().all() else None
        )
        if not issue.empty
        else pd.DataFrame(columns=["bond_type", "week_start_date", "weekly_issue_size"])
    )
    panel = panel.merge(weekly_issue, on=["bond_type", "week_start_date"], how="left", indicator=True)
    panel.loc[panel["_merge"] == "left_only", "weekly_issue_size"] = Decimal(0)
    panel = panel.drop(columns="_merge")
    panel = panel.sort_values(["bond_type", "week_start_date"]).reset_index(drop=True)

    panel["prev8_mean_issue_size"] = None
    panel["prev8_std_issue_size"] = None
    panel["shock_threshold"] = None
    for _, group in panel.groupby("bond_type"):
        for offset in range(ROLLING_WEEKS, len(group)):
            previous = group.iloc[offset - ROLLING_WEEKS:offset]["weekly_issue_size"]
            if previous.isna().any():
                continue
            values = [decimal_value(v) for v in previous]
            mean = sum(values) / ROLLING_WEEKS
            std = (sum((v - mean) ** 2 for v in values) / ROLLING_WEEKS).sqrt()
            idx = group.index[offset]
            panel.at[idx, "prev8_mean_issue_size"] = mean
            panel.at[idx, "prev8_std_issue_size"] = std
            panel.at[idx, "shock_threshold"] = mean + STD_MULTIPLIER * std
    return panel


def find_supply_shocks(panel: pd.DataFrame) -> pd.DataFrame:
    if panel.empty:
        return panel
    signals = panel[
        panel["prev8_std_issue_size"].notna()
        & panel["weekly_issue_size"].notna()
        & panel["week_start_date"].between(SIGNAL_START, LAST_SIGNAL_WEEK_START)
    ].copy()
    signals["是否放量"] = (signals["weekly_issue_size"] > signals["shock_threshold"]).map(
        {True: "放量周", False: "非放量周"}
    )
    return signals.reset_index(drop=True)


def build_event_returns(signals: pd.DataFrame, curve: pd.DataFrame) -> pd.DataFrame:
    if signals.empty or curve.empty:
        return pd.DataFrame(columns=["债券类型", "是否放量", "观察窗口", "yield_change_bp"])

    rows: list[dict[str, object]] = []
    for _, signal in signals.iterrows():
        bond_type = signal["bond_type"]
        week_start = pd.Timestamp(signal["week_start_date"])
        week_end = week_start + pd.Timedelta(days=6)

        curve_slice = curve[
            (curve["bond_type"] == bond_type)
            & (curve["obs_date"] >= week_start)
            & (curve["obs_date"] <= week_end)
        ].sort_values("obs_date")
        if curve_slice.empty:
            continue

        signal_date = curve_slice["obs_date"].max()
        signal_yield = curve_slice.loc[curve_slice["obs_date"] == signal_date, "curve_yield"].iloc[-1]
        if pd.isna(signal_yield):
            continue
        future = (
            curve[(curve["bond_type"] == bond_type) & (curve["obs_date"] > week_end)]
            .sort_values("obs_date")
            .reset_index(drop=True)
        )

        for horizon in HORIZONS:
            if len(future) < horizon:
                continue
            target_yield = future.loc[horizon - 1, "curve_yield"]
            if pd.isna(target_yield):
                continue
            rows.append(
                {
                    "债券类型": bond_type,
                    "是否放量": signal["是否放量"],
                    "观察窗口": f"{horizon}日",
                    "week_start_date": week_start,
                    "baseline_date": signal_date,
                    "target_date": future.loc[horizon - 1, "obs_date"],
                    "yield_change_bp": (decimal_value(target_yield) - decimal_value(signal_yield)) * 10000,
                }
            )

    return pd.DataFrame(rows)


def summarize_events(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty:
        return empty_result()

    summary = (
        events.groupby(["债券类型", "是否放量", "观察窗口"], as_index=False)
        .agg(
            样本数=("yield_change_bp", "count"),
            **{
                "平均变化(bp)": ("yield_change_bp", lambda x: sum(x) / len(x)),
                "中位数变化(bp)": ("yield_change_bp", lambda x: median(x)),
                "上行占比(%)": ("yield_change_bp", lambda x: Decimal(int((x > 0).sum())) / len(x) * 100),
            },
        )
    )

    type_order = {bond_type: idx for idx, bond_type in enumerate(BOND_TYPES)}
    horizon_order = {f"{horizon}日": idx for idx, horizon in enumerate(HORIZONS)}
    summary["type_order"] = summary["债券类型"].map(type_order)
    summary["shock_order"] = summary["是否放量"].map({"放量周": 0, "非放量周": 1})
    summary["horizon_order"] = summary["观察窗口"].map(horizon_order)
    summary = summary.sort_values(["type_order", "shock_order", "horizon_order"]).drop(
        columns=["type_order", "shock_order", "horizon_order"]
    )
    summary["样本数"] = summary["样本数"].astype(int)
    return summary.reset_index(drop=True)[RESULT_COLUMNS]


def format_result(result: pd.DataFrame) -> pd.DataFrame:
    if result.empty:
        return empty_result()

    formatted = result.copy()
    for col in ["平均变化(bp)", "中位数变化(bp)", "上行占比(%)"]:
        formatted[col] = formatted[col].map(
            lambda value: f"{value:.2f}" if pd.notna(value) else ""
        )
    return formatted[RESULT_COLUMNS]


def main() -> pd.DataFrame:
    raw = load_raw_data()
    issue, curve = split_raw_data(raw)
    if curve.empty:
        return empty_result()

    panel = build_weekly_panel(issue)
    signals = find_supply_shocks(panel)
    events = build_event_returns(signals, curve)
    return format_result(summarize_events(events))


if __name__ == "__main__":
    args = parse_args()
    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    main().to_csv(output_path, index=False, encoding="utf-8-sig")
