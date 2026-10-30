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


AS_OF_DATE = pd.Timestamp("2025-06-01")
INDEX_CODE = "000300"
MONTH_COUNT = 12
MOMENTUM_LOOKBACK = 60
OUTPUT_COLUMNS = ["有效月份数", "平均Rank IC", "正IC比例(%)", "平均头尾组收益差"]
SQL_BLOCK_RE = re.compile(
    r"^-- name:\s*(?P<name>[a-zA-Z0-9_]+)\s*$\n(?P<sql>.*?)(?=^-- name:|\Z)",
    re.M | re.S,
)


@dataclass(frozen=True)
class MonthSchedule:
    obs_date: pd.Timestamp
    lookback_date: pd.Timestamp
    entry_date: pd.Timestamp
    month_end: pd.Timestamp


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
        start_date="2023-01-01",
        end_date=AS_OF_DATE.strftime("%Y-%m-%d"),
    )
    if data.empty:
        raise ValueError("No trading days returned")
    return pd.DatetimeIndex(
        pd.to_datetime(data["trade_date"]).sort_values().drop_duplicates()
    )


def build_schedule(trading_days: pd.DatetimeIndex) -> list[MonthSchedule]:
    calendar = pd.DataFrame({"trade_date": trading_days})
    calendar["month"] = calendar["trade_date"].dt.to_period("M")
    month_ends = calendar.groupby("month", sort=True)["trade_date"].max()

    current_month = AS_OF_DATE.to_period("M")
    complete_months = month_ends[month_ends.index < current_month].tail(MONTH_COUNT)
    if len(complete_months) != MONTH_COUNT:
        raise ValueError(f"Need {MONTH_COUNT} complete months, got {len(complete_months)}")

    positions = pd.Series(range(len(trading_days)), index=trading_days)
    schedule: list[MonthSchedule] = []
    for month, month_end in complete_months.items():
        previous_month = month - 1
        if previous_month not in month_ends.index:
            raise ValueError(f"Missing previous month end for {month}")
        obs_date = pd.Timestamp(month_ends.loc[previous_month])
        obs_pos = int(positions.loc[obs_date])
        if obs_pos < MOMENTUM_LOOKBACK or obs_pos + 1 >= len(trading_days):
            raise ValueError(f"Insufficient trading calendar around {obs_date:%Y-%m-%d}")
        entry_date = pd.Timestamp(trading_days[obs_pos + 1])
        if entry_date.to_period("M") != month:
            raise ValueError(f"Entry date does not belong to return month {month}")
        schedule.append(
            MonthSchedule(
                obs_date=obs_date,
                lookback_date=pd.Timestamp(trading_days[obs_pos - MOMENTUM_LOOKBACK]),
                entry_date=entry_date,
                month_end=pd.Timestamp(month_end),
            )
        )
    return schedule


def sql_schedule_rows(schedule: list[MonthSchedule]) -> str:
    return "\nUNION ALL\n".join(
        "SELECT DATE('{obs}') AS obs_date, DATE('{lookback}') AS lookback_date, "
        "DATE('{entry}') AS entry_date, DATE('{month_end}') AS month_end".format(
            obs=item.obs_date.strftime("%Y-%m-%d"),
            lookback=item.lookback_date.strftime("%Y-%m-%d"),
            entry=item.entry_date.strftime("%Y-%m-%d"),
            month_end=item.month_end.strftime("%Y-%m-%d"),
        )
        for item in schedule
    )


def fetch_factor_panel(schedule: list[MonthSchedule]) -> pd.DataFrame:
    panel = query_block(
        "factor_panel",
        timeout=600,
        schedule_rows=sql_schedule_rows(schedule),
        index_code=INDEX_CODE,
    )
    if panel.empty:
        raise ValueError("No factor observations returned")
    panel["obs_date"] = pd.to_datetime(panel["obs_date"])
    for column in ["trend_factor", "stock_return"]:
        panel[column] = pd.to_numeric(panel[column], errors="coerce")
    return panel.dropna(subset=["obs_date", "trend_factor", "stock_return"])


def summarize(panel: pd.DataFrame) -> pd.DataFrame:
    monthly_rows: list[dict[str, float | pd.Timestamp]] = []
    for obs_date, section in panel.groupby("obs_date", sort=True):
        section = section[["trend_factor", "stock_return"]].dropna().copy()
        if len(section) < 5:
            continue
        rank_ic = section["trend_factor"].corr(
            section["stock_return"], method="spearman"
        )
        if pd.isna(rank_ic):
            continue
        # Injected background: with ORDER BY metric DESC, top X% means PERCENT_RANK() <= X; the bottom group mirrors it.
        n = len(section)
        top_rank = (section["trend_factor"].rank(method="min", ascending=False) - 1) / (n - 1)
        bottom_rank = (section["trend_factor"].rank(method="min", ascending=True) - 1) / (n - 1)
        top_return = section.loc[top_rank <= 0.2, "stock_return"].mean()
        bottom_return = section.loc[bottom_rank <= 0.2, "stock_return"].mean()
        monthly_rows.append(
            {
                "obs_date": pd.Timestamp(obs_date),
                "rank_ic": float(rank_ic),
                "top_bottom_return": float(top_return - bottom_return),
            }
        )

    monthly = pd.DataFrame(monthly_rows)
    if monthly.empty:
        raise ValueError("No valid monthly IC observations")
    return pd.DataFrame(
        [
            {
                "有效月份数": int(len(monthly)),
                "平均Rank IC": f"{monthly['rank_ic'].mean():.4f}",
                "正IC比例(%)": f"{(monthly['rank_ic'] > 0).mean() * 100:.2f}",
                "平均头尾组收益差": f"{monthly['top_bottom_return'].mean():.4f}",
            }
        ],
        columns=OUTPUT_COLUMNS,
    )


def main() -> pd.DataFrame:
    with shared_connection_session():
        trading_days = fetch_trading_days()
        schedule = build_schedule(trading_days)
        result = summarize(fetch_factor_panel(schedule))
    output_path = Path(__file__).with_name("result.csv")
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
