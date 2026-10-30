#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from string import Template

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe, shared_connection_session


AS_OF_DATE = pd.Timestamp("2026-02-08")
INDEX_CODE = "000906"
FORWARD_DAYS = 60
OUTPUT_COLUMNS = ["n_periods", "mean_ic", "std_ic", "positive_ic_ratio"]
SQL_BLOCK_RE = re.compile(
    r"^-- name:\s*(?P<name>[a-zA-Z0-9_]+)\s*$\n(?P<sql>.*?)(?=^-- name:|\Z)",
    re.M | re.S,
)


@dataclass(frozen=True)
class Observation:
    cutoff_date: pd.Timestamp
    obs_date: pd.Timestamp
    forward_date: pd.Timestamp
    report_date: pd.Timestamp
    prior_same_date: pd.Timestamp
    prior_annual_date: pd.Timestamp


def load_sql_blocks() -> dict[str, str]:
    sql_path = Path(__file__).with_name("refer.sql")
    text = sql_path.read_text(encoding="utf-8")
    blocks = {
        match.group("name"): match.group("sql").strip()
        for match in SQL_BLOCK_RE.finditer(text)
    }
    if not blocks:
        raise ValueError(f"No named SQL blocks found in {sql_path}")
    return blocks


SQL_BLOCKS = load_sql_blocks()


def query_block(name: str, timeout: int = 600, **params: object) -> pd.DataFrame:
    if name not in SQL_BLOCKS:
        raise KeyError(f"Missing SQL block: {name}")
    sql = Template(SQL_BLOCKS[name]).substitute(
        **{key: str(value) for key, value in params.items()}
    )
    result = query_to_dataframe(sql, max_rows=0, timeout=timeout)
    if result is None:
        raise RuntimeError(f"SQL returned None: {name}")
    return result


def fetch_trading_days() -> pd.DatetimeIndex:
    data = query_block(
        "trading_days",
        timeout=120,
        start_date="2024-04-01",
        end_date=AS_OF_DATE.strftime("%Y-%m-%d"),
    )
    if data.empty:
        raise ValueError("No trading days returned")
    return pd.DatetimeIndex(
        pd.to_datetime(data["trade_date"]).sort_values().drop_duplicates()
    )


def build_schedule(trading_days: pd.DatetimeIndex) -> list[Observation]:
    rows: list[Observation] = []
    for year in (2024, 2025):
        for month, day, report_month, report_day in (
            (4, 30, 3, 31),
            (8, 31, 6, 30),
            (10, 31, 9, 30),
        ):
            cutoff = pd.Timestamp(year=year, month=month, day=day)
            obs_pos = int(trading_days.searchsorted(cutoff, side="right"))
            forward_pos = obs_pos + FORWARD_DAYS
            if obs_pos >= len(trading_days) or forward_pos >= len(trading_days):
                raise ValueError(f"Incomplete 60-day window after {cutoff:%Y-%m-%d}")
            report_date = pd.Timestamp(year=year, month=report_month, day=report_day)
            rows.append(
                Observation(
                    cutoff_date=cutoff,
                    obs_date=pd.Timestamp(trading_days[obs_pos]),
                    forward_date=pd.Timestamp(trading_days[forward_pos]),
                    report_date=report_date,
                    prior_same_date=report_date - pd.DateOffset(years=1),
                    prior_annual_date=pd.Timestamp(year=year - 1, month=12, day=31),
                )
            )
    return rows


def sql_schedule_rows(schedule: list[Observation]) -> str:
    return "\nUNION ALL\n".join(
        "SELECT DATE('{cutoff}') AS cutoff_date, DATE('{obs}') AS obs_date, "
        "DATE('{forward}') AS forward_date, DATE('{report}') AS report_date, "
        "DATE('{prior_same}') AS prior_same_date, "
        "DATE('{prior_annual}') AS prior_annual_date".format(
            cutoff=item.cutoff_date.strftime("%Y-%m-%d"),
            obs=item.obs_date.strftime("%Y-%m-%d"),
            forward=item.forward_date.strftime("%Y-%m-%d"),
            report=item.report_date.strftime("%Y-%m-%d"),
            prior_same=item.prior_same_date.strftime("%Y-%m-%d"),
            prior_annual=item.prior_annual_date.strftime("%Y-%m-%d"),
        )
        for item in schedule
    )


def fetch_factor_panel(schedule: list[Observation]) -> pd.DataFrame:
    panel = query_block(
        "factor_panel",
        timeout=700,
        schedule_rows=sql_schedule_rows(schedule),
        index_code=INDEX_CODE,
    )
    if panel.empty:
        raise ValueError("No low-accrual observations returned")
    panel["obs_date"] = pd.to_datetime(panel["obs_date"])
    for column in ["low_accrual_score", "forward_return"]:
        panel[column] = pd.to_numeric(panel[column], errors="coerce")
    return panel.dropna(subset=["obs_date", "low_accrual_score", "forward_return"])


def summarize(panel: pd.DataFrame) -> pd.DataFrame:
    ic_values: list[float] = []
    for _, section in panel.groupby("obs_date", sort=True):
        pair = section[["low_accrual_score", "forward_return"]].dropna()
        if len(pair) < 2:
            continue
        ic = pair["low_accrual_score"].corr(
            pair["forward_return"], method="spearman"
        )
        if pd.notna(ic):
            ic_values.append(float(ic))
    if not ic_values:
        raise ValueError("No valid cross-sectional IC values")

    ic = pd.Series(ic_values, dtype="float64")
    std_ic = ic.std(ddof=1) if len(ic) > 1 else 0.0
    return pd.DataFrame(
        [
            {
                "n_periods": int(len(ic)),
                "mean_ic": f"{ic.mean():.4f}",
                "std_ic": f"{std_ic:.4f}",
                "positive_ic_ratio": f"{(ic > 0).mean() * 100:.2f}",
            }
        ],
        columns=OUTPUT_COLUMNS,
    )


def main() -> pd.DataFrame:
    with shared_connection_session():
        schedule = build_schedule(fetch_trading_days())
        result = summarize(fetch_factor_panel(schedule))
    output_path = Path(__file__).with_name("result.csv")
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
