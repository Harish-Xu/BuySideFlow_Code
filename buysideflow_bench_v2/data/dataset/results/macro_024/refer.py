#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from string import Template

import pandas as pd

os.environ.setdefault("DB_CONNECT_TIMEOUT_SEC", "10")

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


START_DATE = "2021-01-01"
OBSERVE_DATE = "2025-11-15"
PMI_CODE = 110166523
PMI_THRESHOLD = 50.0
MIN_SAME_DIRECTION_MONTH_GAP = 2
GOV10_INDICATOR_CODE = 600020013  # 中债国债到期收益率:10年:日
HORIZON_CURVE_DAYS = 20
OUTPUT_COLUMNS = [
    "事件日",
    "方向",
    "PMI月值",
    "基准收益率",
    "20日后收益率",
    "收益率变化",
]
SQL_BLOCK_RE = re.compile(
    r"^-- name:\s*(?P<name>[a-zA-Z0-9_]+)\s*$\n(?P<sql>.*?)(?=^-- name:|\Z)",
    re.M | re.S,
)


def query(sql: str, timeout: int = 600, retries: int = 2) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            df = query_to_dataframe(sql, max_rows=0, timeout=timeout)
            if df is None:
                raise RuntimeError("SQL returned None")
            return df
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            print(
                f"query_retry={attempt + 1}/{retries} "
                f"error={type(exc).__name__}: {exc}",
                flush=True,
            )
    raise last_error if last_error is not None else RuntimeError("SQL query failed")


def load_sql_blocks() -> dict[str, str]:
    sql_path = Path(__file__).with_name("refer.sql")
    sql_text = sql_path.read_text(encoding="utf-8")
    blocks = {
        match.group("name"): match.group("sql").strip()
        for match in SQL_BLOCK_RE.finditer(sql_text)
    }
    if not blocks:
        raise ValueError(f"No named SQL blocks found in {sql_path}")
    return blocks


SQL_BLOCKS = load_sql_blocks()


def render_sql(name: str, **params: str | int) -> str:
    if name not in SQL_BLOCKS:
        raise KeyError(f"Missing SQL block: {name}")
    return Template(SQL_BLOCKS[name]).substitute(**params)


def query_block(name: str, timeout: int = 600, **params: str | int) -> pd.DataFrame:
    return query(render_sql(name, **params), timeout=timeout)


def fetch_pmi_values() -> pd.DataFrame:
    df = query_block(
        "pmi_values",
        timeout=180,
        pmi_code=PMI_CODE,
        start_date=START_DATE,
        observe_date=OBSERVE_DATE,
    )
    if df.empty:
        raise ValueError("PMI query returned no rows")
    df["month_end"] = pd.to_datetime(df["month_end"])
    df["publish_date"] = pd.to_datetime(df["publish_date"])
    df["pmi_value"] = pd.to_numeric(df["pmi_value"], errors="raise")
    return df.sort_values("month_end").reset_index(drop=True)


def build_crossing_events(pmi: pd.DataFrame) -> pd.DataFrame:
    data = pmi.copy()
    data["prev_pmi"] = data["pmi_value"].shift(1)
    data["direction"] = ""
    data.loc[
        (data["prev_pmi"] < PMI_THRESHOLD) & (data["pmi_value"] >= PMI_THRESHOLD),
        "direction",
    ] = "上穿"
    data.loc[
        (data["prev_pmi"] >= PMI_THRESHOLD) & (data["pmi_value"] < PMI_THRESHOLD),
        "direction",
    ] = "下破"
    raw_events = data[
        (data["publish_date"] >= pd.Timestamp(START_DATE)) & data["direction"].ne("")
    ].sort_values("publish_date", kind="stable").copy()
    if raw_events.empty:
        raise ValueError("No PMI crossing events found")

    rows: list[dict[str, object]] = []
    last_kept_date_by_direction: dict[str, pd.Timestamp] = {}
    for row in raw_events.to_dict("records"):
        direction = str(row["direction"])
        event_date = pd.Timestamp(row["publish_date"])
        last_date = last_kept_date_by_direction.get(direction)
        if last_date is not None and event_date < last_date + pd.DateOffset(
            months=MIN_SAME_DIRECTION_MONTH_GAP
        ):
            continue
        rows.append(row)
        last_kept_date_by_direction[direction] = event_date

    events = pd.DataFrame(rows)
    if events.empty:
        raise ValueError("No PMI crossing events survived same-direction de-duplication")
    return events.sort_values("publish_date").reset_index(drop=True)


def fetch_curve(events: pd.DataFrame) -> pd.DataFrame:
    min_event_date = pd.Timestamp(events["publish_date"].min())
    curve_start_date = (min_event_date - pd.DateOffset(months=1)).strftime("%Y-%m-%d")
    df = query_block(
        "gov10_curve",
        timeout=240,
        indicator_code=GOV10_INDICATOR_CODE,
        curve_start_date=curve_start_date,
        curve_end_date=OBSERVE_DATE,
    )
    if df.empty:
        raise ValueError("10Y ChinaBond government curve query returned no rows")
    df["curve_date"] = pd.to_datetime(df["curve_date"])
    df["yield_10y"] = pd.to_numeric(df["yield_10y"], errors="raise")
    return df.sort_values("curve_date").drop_duplicates("curve_date", keep="last").reset_index(drop=True)


def attach_curve_reaction(events: pd.DataFrame, curve: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    curve_dates = curve["curve_date"].reset_index(drop=True)
    curve_yields = curve.set_index("curve_date")["yield_10y"]

    rows: list[dict[str, object]] = []
    skipped_rows: list[dict[str, object]] = []
    for row in events.to_dict("records"):
        event_date = pd.Timestamp(row["publish_date"])
        base_pos = int(curve_dates.searchsorted(event_date, side="left")) - 1
        horizon_pos = int(curve_dates.searchsorted(event_date, side="right")) + HORIZON_CURVE_DAYS - 1
        if base_pos < 0:
            skipped_rows.append(
                {
                    "event_date": event_date,
                    "month_end": pd.Timestamp(row["month_end"]),
                    "direction": row["direction"],
                    "pmi_value": float(row["pmi_value"]),
                    "reason": "缺少事件日前一曲线交易日",
                }
            )
            continue
        if horizon_pos >= len(curve_dates):
            skipped_rows.append(
                {
                    "event_date": event_date,
                    "month_end": pd.Timestamp(row["month_end"]),
                    "direction": row["direction"],
                    "pmi_value": float(row["pmi_value"]),
                    "reason": f"缺少事件后第{HORIZON_CURVE_DAYS}个曲线交易日",
                }
            )
            continue

        base_curve_date = pd.Timestamp(curve_dates.iloc[base_pos])
        horizon_curve_date = pd.Timestamp(curve_dates.iloc[horizon_pos])
        base_yield = float(curve_yields.loc[base_curve_date])
        horizon_yield = float(curve_yields.loc[horizon_curve_date])
        rows.append(
            {
                "event_date": event_date,
                "direction": row["direction"],
                "pmi_value": float(row["pmi_value"]),
                "base_yield_pct": base_yield * 100.0,
                "horizon_yield_pct": horizon_yield * 100.0,
                "yield_change_bp": (horizon_yield - base_yield) * 10000.0,
            }
        )

    result = pd.DataFrame(rows)
    skipped = pd.DataFrame(skipped_rows)
    if result.empty:
        raise ValueError("No PMI crossing event has both baseline and 20th future curve dates")
    return (
        result.sort_values("event_date").reset_index(drop=True),
        skipped.sort_values("event_date").reset_index(drop=True)
        if not skipped.empty
        else pd.DataFrame(columns=["event_date", "month_end", "direction", "pmi_value", "reason"]),
    )


def format_result(result: pd.DataFrame) -> pd.DataFrame:
    formatted = pd.DataFrame(
        {
            "事件日": result["event_date"].dt.strftime("%Y-%m-%d"),
            "方向": result["direction"],
            "PMI月值": result["pmi_value"].map(lambda value: f"{value:.1f}"),
            "基准收益率": result["base_yield_pct"].map(lambda value: f"{value:.4f}"),
            "20日后收益率": result["horizon_yield_pct"].map(lambda value: f"{value:.4f}"),
            "收益率变化": result["yield_change_bp"].map(lambda value: f"{value:.2f}"),
        }
    )
    return formatted[OUTPUT_COLUMNS]


def main() -> pd.DataFrame:
    script_dir = Path(__file__).resolve().parent
    output_path = script_dir / "result.csv"

    pmi = fetch_pmi_values()
    events = build_crossing_events(pmi)
    curve = fetch_curve(events)
    reactions, skipped = attach_curve_reaction(events, curve)
    result = format_result(reactions)

    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    if not skipped.empty:
        print(f"excluded_events={len(skipped)}", flush=True)
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
