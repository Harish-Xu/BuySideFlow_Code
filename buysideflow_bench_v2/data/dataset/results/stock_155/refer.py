#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe, shared_connection_session


SCRIPT_DIR = Path(__file__).resolve().parent
SQL_PATH = SCRIPT_DIR / "refer.sql"
OUTPUT_PATH = SCRIPT_DIR / "result.csv"
MONTH_COUNT = 12
OUTPUT_COLUMNS = ["factor", "valid_months", "mean_ic", "std_ic", "positive_ratio"]
FACTOR_COLUMNS = [
    ("总市值", "total_market_value"),
    ("BP（1/PB）", "bp"),
    ("营业收入同比增速", "revenue_yoy"),
]
SQL_BLOCK_RE = re.compile(
    r"^-- name:\s*(?P<name>[a-zA-Z0-9_]+)\s*$\n(?P<sql>.*?)(?=^-- name:|\Z)",
    re.M | re.S,
)


@dataclass(frozen=True)
class MonthSchedule:
    obs_date: pd.Timestamp
    month_end: pd.Timestamp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build stock_155 monthly IC summary.")
    parser.add_argument("--as-of-date", default="2025-12-31")
    parser.add_argument("--output-path", default=str(OUTPUT_PATH))
    return parser.parse_args()


def load_sql_blocks() -> dict[str, str]:
    text = SQL_PATH.read_text(encoding="utf-8")
    blocks = {
        match.group("name"): match.group("sql").strip()
        for match in SQL_BLOCK_RE.finditer(text)
    }
    if not blocks:
        raise ValueError(f"No named SQL blocks found in {SQL_PATH}")
    return blocks


SQL_BLOCKS = load_sql_blocks()


def render_sql(name: str, **params: object) -> str:
    if name not in SQL_BLOCKS:
        raise KeyError(f"Missing SQL block: {name}")
    return SQL_BLOCKS[name].format(**{key: str(value) for key, value in params.items()})


def query_block(name: str, timeout: int = 600, **params: object) -> pd.DataFrame:
    result = query_to_dataframe(render_sql(name, **params), max_rows=0, timeout=timeout)
    if result is None:
        raise RuntimeError(f"SQL returned None: {name}")
    return result


def sql_date_rows(dates: list[pd.Timestamp], column_name: str = "obs_date") -> str:
    return "\n    UNION ALL\n    ".join(
        f"SELECT DATE('{pd.Timestamp(date):%Y-%m-%d}') AS {column_name}"
        for date in dates
    )


def sql_schedule_rows(schedule: list[MonthSchedule]) -> str:
    return "\n    UNION ALL\n    ".join(
        "SELECT DATE('{obs}') AS obs_date, DATE('{end}') AS month_end".format(
            obs=item.obs_date.strftime("%Y-%m-%d"),
            end=item.month_end.strftime("%Y-%m-%d"),
        )
        for item in schedule
    )


def sql_int_list(values: pd.Series | list[int]) -> str:
    unique = sorted(pd.Series(values).dropna().astype(int).unique().tolist())
    if not unique:
        raise ValueError("No integer values available for SQL IN clause")
    return ",".join(str(value) for value in unique)


def build_schedule(as_of_date: str) -> list[MonthSchedule]:
    data = query_block("month_ends", as_of_date=as_of_date, timeout=120)
    if data.empty:
        raise ValueError("No month ends returned")
    month_ends = pd.DatetimeIndex(
        pd.to_datetime(data["month_end"]).sort_values().drop_duplicates()
    )
    if len(month_ends) < MONTH_COUNT + 1:
        raise ValueError(f"Need at least {MONTH_COUNT + 1} month ends")
    selected = month_ends[-(MONTH_COUNT + 1) :]
    return [
        MonthSchedule(pd.Timestamp(obs), pd.Timestamp(end))
        for obs, end in zip(selected[:-1], selected[1:])
    ]


def fetch_base_panel(schedule: list[MonthSchedule]) -> pd.DataFrame:
    frames = [
        query_block(
            "base_panel",
            schedule_rows=sql_schedule_rows(schedule[start : start + 3]),
            timeout=300,
        )
        for start in range(0, len(schedule), 3)
    ]
    base = pd.concat(frames, ignore_index=True)
    if base.empty:
        raise ValueError("No monthly stock panel returned")

    base["obs_date"] = pd.to_datetime(base["obs_date"])
    base["INNERCODE"] = pd.to_numeric(base["INNERCODE"], errors="coerce").astype("Int64")
    base["COMPANYCODE"] = pd.to_numeric(base["COMPANYCODE"], errors="coerce").astype("Int64")
    for column in ["total_market_value", "PB", "revenue_yoy", "stock_return"]:
        base[column] = pd.to_numeric(base[column], errors="coerce")
    base = base.dropna(subset=["obs_date", "INNERCODE", "COMPANYCODE", "stock_return"])
    base = remove_st(base)
    if base.empty:
        raise ValueError("No rows remain after listing and trading filters")
    return base.reset_index(drop=True)


def remove_st(base: pd.DataFrame) -> pd.DataFrame:
    risk = query_block(
        "st_risk",
        obs_date_rows=sql_date_rows(sorted(base["obs_date"].unique())),
        innercodes=sql_int_list(base["INNERCODE"]),
        timeout=300,
    )
    if risk.empty:
        return base
    risk["obs_date"] = pd.to_datetime(risk["obs_date"])
    risk["INNERCODE"] = pd.to_numeric(risk["INNERCODE"], errors="coerce").astype("Int64")
    keys = set(zip(risk["obs_date"], risk["INNERCODE"].astype(int)))
    keep = [
        (row.obs_date, int(row.INNERCODE)) not in keys
        for row in base[["obs_date", "INNERCODE"]].itertuples(index=False)
    ]
    return base.loc[keep].copy()


def summarize(panel: pd.DataFrame) -> pd.DataFrame:
    panel = panel.copy()
    panel["bp"] = np.where(panel["PB"] != 0, 1.0 / panel["PB"], np.nan)
    panel = panel.replace([np.inf, -np.inf], np.nan)

    rows: list[dict[str, object]] = []
    for factor_name, factor_column in FACTOR_COLUMNS:
        monthly_ic: list[float] = []
        for _, section in panel.groupby("obs_date", sort=True):
            pair = section[[factor_column, "stock_return"]].dropna()
            if len(pair) < 2:
                continue
            ic = pair[factor_column].corr(pair["stock_return"], method="spearman")
            if pd.notna(ic):
                monthly_ic.append(float(ic))
        values = pd.Series(monthly_ic, dtype="float64")
        rows.append(
            {
                "factor": factor_name,
                "valid_months": int(len(values)),
                "mean_ic": "" if values.empty else f"{values.mean():.4f}",
                "std_ic": "" if values.empty else f"{values.std(ddof=1):.4f}",
                "positive_ratio": ""
                if values.empty
                else f"{(values > 0).mean() * 100:.2f}",
            }
        )
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def build_result(as_of_date: str) -> pd.DataFrame:
    schedule = build_schedule(as_of_date)
    return summarize(fetch_base_panel(schedule))


def main() -> pd.DataFrame:
    args = parse_args()
    as_of_date = pd.Timestamp(args.as_of_date).strftime("%Y-%m-%d")
    with shared_connection_session():
        result = build_result(as_of_date)
    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
