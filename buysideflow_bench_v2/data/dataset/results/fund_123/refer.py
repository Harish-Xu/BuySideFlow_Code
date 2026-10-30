#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmark_paths import load_benchmark_env
from sweagent.text2sql.db_connector import query_to_dataframe
from sweagent.text2sql.provenance import require_db_snapshot_id


START_DAY = "2024-12-31"
END_DAY = "2025-12-31"
CUTOFF = "2026-01-01"
OUTPUT_COLUMNS = [
    "基金代码", "基金简称", "规模报告期", "合并规模", "全年收益率", "年化波动率", "全年超额收益率",
]
FUND_POOL_SQL = globals().get("sql_code") or Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
CALENDAR_SQL = f"""
SELECT DATE(TRADINGDATE) AS trading_day, SECUMARKET, IFTRADINGDAY
FROM qt_tradingdaynew
WHERE SECUMARKET IN (83, 90)
  AND TRADINGDATE >= '{START_DAY}' AND TRADINGDATE < '{CUTOFF}'
ORDER BY TRADINGDATE, SECUMARKET
"""
INDEX_ID_SQL = """
SELECT INNERCODE FROM secumain
WHERE SECUCATEGORY = 4 AND SECUCODE = '000300' AND SECUMARKET = 83
"""
INDEX_SQL_TEMPLATE = """
WITH ranked_quotes AS (
    SELECT DATE(q.TRADINGDAY) AS trading_day, q.CLOSEPRICE,
           ROW_NUMBER() OVER (
               PARTITION BY DATE(q.TRADINGDAY) ORDER BY q.XGRQ DESC, q.ID DESC
           ) AS version_rank
    FROM qt_indexquote q
    WHERE q.INNERCODE = {innercode}
      AND q.TRADINGDAY >= '2024-12-31' AND q.TRADINGDAY < '2026-01-01'
)
SELECT trading_day, CLOSEPRICE FROM ranked_quotes
WHERE version_rank = 1 ORDER BY trading_day
"""
NAV_SQL_TEMPLATE = """
WITH visible_nav AS (
    SELECT n.INNERCODE, DATE(n.TRADINGDAY) AS trading_day,
           n.UNITNVRESTORED AS restored_nav, n.INSERTTIME,
           ROW_NUMBER() OVER (
               PARTITION BY n.INNERCODE, DATE(n.TRADINGDAY)
               ORDER BY n.INSERTTIME DESC, n.ID DESC
           ) AS version_rank
    FROM mf_fundnetvalueretrans n
    WHERE n.INNERCODE IN ({innercodes})
      AND n.TRADINGDAY >= '2024-12-31' AND n.TRADINGDAY < '2026-01-01'
      AND n.INSERTTIME < '2026-01-01'
)
SELECT INNERCODE, trading_day, restored_nav, INSERTTIME
FROM visible_nav WHERE version_rank = 1
ORDER BY INNERCODE, trading_day
"""


def trading_days(calendar: pd.DataFrame) -> pd.DatetimeIndex:
    calendar = calendar.copy()
    calendar["trading_day"] = pd.to_datetime(calendar["trading_day"]).dt.normalize()
    expected = pd.date_range(START_DAY, END_DAY)
    for market in (83, 90):
        rows = calendar.loc[calendar["SECUMARKET"] == market]
        if rows["trading_day"].duplicated().any():
            raise ValueError(f"Duplicate calendar entries for market {market}")
        if not pd.DatetimeIndex(rows["trading_day"]).sort_values().equals(expected):
            raise ValueError(f"Incomplete daily calendar for market {market}")
        if not rows["IFTRADINGDAY"].isin([1, 2]).all():
            raise ValueError(f"Unknown calendar status for market {market}")
    days = pd.DatetimeIndex(calendar.loc[calendar["IFTRADINGDAY"] == 1, "trading_day"].unique()).sort_values()
    if days[0] != pd.Timestamp(START_DAY) or days[-1] != pd.Timestamp(END_DAY):
        raise ValueError("Required year endpoints are absent from the exchange calendar")
    return days


def fetch_inputs(query=query_to_dataframe):
    days = trading_days(query(CALENDAR_SQL, max_rows=0, timeout=120))
    pool = query(FUND_POOL_SQL, max_rows=0, timeout=300)
    if pool.empty:
        raise ValueError("No observable active-equity main shares with disclosed merged scale")
    pool["INNERCODE"] = pd.to_numeric(pool["INNERCODE"], errors="raise").astype(int)
    if pool["INNERCODE"].duplicated().any() or pool["fund_code"].duplicated().any():
        raise ValueError("Duplicate main-share fund identifiers")
    index_ids = query(INDEX_ID_SQL, max_rows=0, timeout=120)
    if len(index_ids) != 1:
        raise ValueError("CSI 300 price index identifier is missing or ambiguous")
    benchmark = query(
        INDEX_SQL_TEMPLATE.format(innercode=int(index_ids.iloc[0]["INNERCODE"])),
        max_rows=0, timeout=120,
    )
    chunks = []
    for start in range(0, len(pool), 200):
        codes = ",".join(str(int(value)) for value in pool["INNERCODE"].iloc[start:start + 200])
        chunks.append(query(NAV_SQL_TEMPLATE.format(innercodes=codes), max_rows=0, timeout=300))
    nav = pd.concat(chunks, ignore_index=True)
    if nav.empty:
        nav = pd.DataFrame(columns=["INNERCODE", "trading_day", "restored_nav", "INSERTTIME"])
    return days, pool, benchmark, nav


def path_metrics(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    daily_returns = values[1:] / values[:-1] - 1.0
    return values[-1] / values[0] - 1.0, np.std(daily_returns, axis=0, ddof=0) * np.sqrt(252.0)


def calculate(days, pool, benchmark, nav):
    benchmark = benchmark.copy()
    benchmark["trading_day"] = pd.to_datetime(benchmark["trading_day"]).dt.normalize()
    if benchmark["trading_day"].duplicated().any():
        raise ValueError("Duplicate benchmark dates after version selection")
    index_values = pd.to_numeric(
        benchmark.set_index("trading_day")["CLOSEPRICE"], errors="raise",
    ).reindex(days).to_numpy(dtype=float)
    if not (np.isfinite(index_values) & (index_values > 0)).all():
        raise ValueError("CSI 300 lacks positive prices on the complete required calendar")
    benchmark_return, benchmark_vol = path_metrics(index_values)

    nav = nav.copy()
    nav["trading_day"] = pd.to_datetime(nav["trading_day"]).dt.normalize()
    nav["restored_nav"] = pd.to_numeric(nav["restored_nav"], errors="raise")
    if nav.duplicated(["INNERCODE", "trading_day"]).any():
        raise ValueError("Duplicate fund NAV dates after visible-version selection")
    # Reindex before computing returns: adjacent sparse observations are not daily returns.
    panel = nav.pivot(index="trading_day", columns="INNERCODE", values="restored_nav")
    panel = panel.reindex(index=days, columns=pool["INNERCODE"])
    valid = np.isfinite(panel) & (panel > 0)
    complete = valid.all(axis=0)
    coverage = pool[["INNERCODE", "fund_code"]].copy().set_index("INNERCODE")
    coverage["required_nav_days"] = len(days)
    coverage["valid_nav_days"] = valid.sum(axis=0)
    coverage["start_available"] = valid.iloc[0]
    coverage["end_available"] = valid.iloc[-1]
    coverage["complete"] = complete
    metrics = pool.set_index("INNERCODE").loc[complete[complete].index].copy()
    if not metrics.empty:
        metrics["year_return"], metrics["annual_vol"] = path_metrics(panel.loc[:, complete].to_numpy(dtype=float))
    else:
        metrics["year_return"] = pd.Series(dtype=float)
        metrics["annual_vol"] = pd.Series(dtype=float)
    metrics["excess_return"] = metrics["year_return"] - benchmark_return
    metrics["eligible"] = (
        (metrics["year_return"] > benchmark_return)
        & (metrics["annual_vol"] < benchmark_vol) & (metrics["annual_vol"] > 0)
    )
    metrics["score"] = metrics["excess_return"] / metrics["annual_vol"].where(metrics["annual_vol"] > 0)
    selected = metrics.loc[metrics["eligible"]].sort_values(
        ["score", "fund_code"], ascending=[False, True], kind="stable",
    ).head(10)
    result = pd.DataFrame({
        "基金代码": selected["fund_code"],
        "基金简称": selected["fund_name"],
        "规模报告期": pd.to_datetime(selected["scale_date"]).dt.strftime("%Y-%m-%d"),
        "合并规模": pd.to_numeric(selected["COMBNVI"], errors="raise") / 1e8,
        "全年收益率": selected["year_return"] * 100,
        "年化波动率": selected["annual_vol"] * 100,
        "全年超额收益率": selected["excess_return"] * 100,
    }, columns=OUTPUT_COLUMNS).reset_index(drop=True)
    stats = {
        "required_nav_days": len(days), "daily_return_count": len(days) - 1,
        "pool_count": len(pool), "complete_fund_count": int(complete.sum()),
        "eligible_fund_count": int(metrics["eligible"].sum()),
        "benchmark_year_return": float(benchmark_return),
        "benchmark_annual_vol": float(benchmark_vol),
    }
    return result, coverage.reset_index(), metrics.reset_index(), stats


def format_result(result):
    result = result.copy()
    for column in OUTPUT_COLUMNS[3:]:
        result[column] = result[column].map(lambda value: f"{value:.2f}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Full-calendar 2025 active-equity fund screen")
    parser.add_argument("--output-path", type=Path, default=Path(__file__).with_name("result.csv"))
    args = parser.parse_args()
    load_benchmark_env()
    require_db_snapshot_id()
    result, coverage, metrics, stats = calculate(*fetch_inputs())
    if not stats["complete_fund_count"]:
        raise ValueError("No fund has observable positive NAV on all required dates; reference coverage unavailable")
    result = format_result(result)
    if not globals().get("sql_code"):
        args.output_path.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(args.output_path, index=False, encoding="utf-8-sig")
    return result


if __name__ == "__main__":
    result = main()
    print(result)
