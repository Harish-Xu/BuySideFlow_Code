#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = "2025-03-21"
HISTORY_START = "2024-01-01"
BOND_MARKETS = [83, 89, 90]
RESULT_COLUMNS = [
    "债券代码",
    "简称",
    "估值日",
    "剩余期限",
    "修正久期",
    "估值收益率",
    "收益率分位",
    "近20日成交天数",
]


def sql_list(values: list[int]) -> str:
    return ",".join(str(int(v)) for v in values)


def fetch_recent_trade_dates() -> list[str]:
    markets_sql = sql_list(BOND_MARKETS)
    sql = f"""
    SELECT DISTINCT DATE(TRADINGDATE) AS trading_day
    FROM qt_tradingdaynew
    WHERE TRADINGDATE <= '{AS_OF_DATE}'
      AND IFTRADINGDAY = 1
      AND SECUMARKET IN ({markets_sql})
    ORDER BY trading_day DESC
    LIMIT 20
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=120)
    if df.empty:
        return []
    return pd.to_datetime(df["trading_day"], errors="coerce").dropna().dt.strftime("%Y-%m-%d").tolist()


def fetch_current_candidates(base_dir: Path) -> pd.DataFrame:
    sql = (base_dir / "refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df.empty:
        return df
    numeric_cols = ["MAINCODE", "INNERCODE", "remaining_years", "modified_duration", "valuation_yield_pct"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=numeric_cols).copy()
    df["INNERCODE"] = df["INNERCODE"].astype(int)
    df["MAINCODE"] = df["MAINCODE"].astype(int)
    df["valuation_date"] = pd.to_datetime(df["valuation_date"], errors="coerce")
    df = df.dropna(subset=["valuation_date"]).copy()
    return df.sort_values(["MAINCODE", "bond_code", "INNERCODE"]).drop_duplicates("MAINCODE", keep="first")


def fetch_trade20(maincodes: list[int], trade_dates: list[str]) -> pd.DataFrame:
    if not maincodes or not trade_dates:
        return pd.DataFrame(columns=["MAINCODE", "trade_days_20"])
    dates_sql = ",".join(f"'{d}'" for d in trade_dates)
    sql = f"""
    SELECT
        bc.MAINCODE,
        COUNT(DISTINCT DATE(TRADINGDAY)) AS trade_days_20
    FROM bond_basicquote q FORCE INDEX (IX_BOND_BASICQUOTE)
    JOIN bond_code bc
      ON q.INNERCODE = bc.INNERCODE
    WHERE bc.MAINCODE IN ({sql_list(maincodes)})
      AND q.TRADINGDAY IN ({dates_sql})
      AND q.IFTRADING = 1
    GROUP BY bc.MAINCODE
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df.empty:
        return pd.DataFrame(columns=["MAINCODE", "trade_days_20"])
    df["MAINCODE"] = pd.to_numeric(df["MAINCODE"], errors="coerce")
    df["trade_days_20"] = pd.to_numeric(df["trade_days_20"], errors="coerce")
    df = df.dropna(subset=["MAINCODE", "trade_days_20"]).copy()
    df["MAINCODE"] = df["MAINCODE"].astype(int)
    return df


def fetch_history(innercodes: list[int]) -> pd.DataFrame:
    if not innercodes:
        return pd.DataFrame(columns=["INNERCODE", "valuation_date", "valuation_yield_pct"])
    sql = f"""
    SELECT
        INNERCODE,
        DATE(ENDDATE) AS valuation_date,
        VPYIELD AS valuation_yield_pct,
        UPDATETIME,
        INSERTTIME,
        ID
    FROM bond_cbvaluationall FORCE INDEX (IX_BOND_CBVALUATIONALL)
    WHERE INNERCODE IN ({sql_list(innercodes)})
      AND ENDDATE >= '{HISTORY_START}'
      AND ENDDATE < DATE_ADD('{AS_OF_DATE}', INTERVAL 1 DAY)
      AND CREDIBILITYCODE = 1
      AND YIELDCODE = 2
      AND VPYIELD IS NOT NULL
      AND (INSERTTIME IS NULL OR INSERTTIME < DATE_ADD('{AS_OF_DATE}', INTERVAL 1 DAY))
      AND (UPDATETIME IS NULL OR UPDATETIME < DATE_ADD('{AS_OF_DATE}', INTERVAL 1 DAY))
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df.empty:
        return pd.DataFrame(columns=["INNERCODE", "valuation_date", "valuation_yield_pct"])
    df["INNERCODE"] = pd.to_numeric(df["INNERCODE"], errors="coerce")
    df["valuation_yield_pct"] = pd.to_numeric(df["valuation_yield_pct"], errors="coerce")
    df["ID"] = pd.to_numeric(df["ID"], errors="coerce")
    df["UPDATETIME"] = pd.to_datetime(df["UPDATETIME"], errors="coerce")
    df["INSERTTIME"] = pd.to_datetime(df["INSERTTIME"], errors="coerce")
    df["valuation_date"] = pd.to_datetime(df["valuation_date"], errors="coerce")
    df = df.dropna(subset=["INNERCODE", "valuation_date", "valuation_yield_pct"]).copy()
    df["INNERCODE"] = df["INNERCODE"].astype(int)
    df = df.sort_values(
        ["INNERCODE", "valuation_date", "UPDATETIME", "INSERTTIME", "ID"],
        ascending=[True, True, False, False, False],
    )
    return df.drop_duplicates(["INNERCODE", "valuation_date"], keep="first")


def percent_rank_latest(history: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    if history.empty or current.empty:
        return pd.DataFrame(columns=["INNERCODE", "valid_valuation_days", "yield_percentile_pct"])
    current_yield = current[["INNERCODE", "valuation_yield_pct"]].rename(
        columns={"valuation_yield_pct": "current_yield_pct"}
    )
    ranked = history.merge(current_yield, on="INNERCODE", how="inner")
    ranked = ranked.dropna(subset=["valuation_yield_pct", "current_yield_pct"]).copy()
    if ranked.empty:
        return pd.DataFrame(columns=["INNERCODE", "valid_valuation_days", "yield_percentile_pct"])

    ranked["le_current"] = ranked["valuation_yield_pct"] <= ranked["current_yield_pct"]
    summary = (
        ranked.groupby("INNERCODE", as_index=False)
        .agg(
            valid_valuation_days=("valuation_yield_pct", "count"),
            le_current_count=("le_current", "sum"),
        )
        .loc[lambda frame: frame["valid_valuation_days"] >= 60]
        .copy()
    )
    summary["yield_percentile_pct"] = (
        summary["le_current_count"] / summary["valid_valuation_days"] * 100.0
    )
    return summary[["INNERCODE", "valid_valuation_days", "yield_percentile_pct"]]


def fmt_date(value: object) -> str:
    if pd.isna(value):
        return ""
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def fmt_float(value: object) -> str:
    if pd.isna(value):
        return ""
    return f"{float(value):.2f}"


def format_result(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    result = pd.DataFrame(
        {
            "债券代码": raw["bond_code"].astype(str),
            "简称": raw["bond_abbr"].astype(str),
            "估值日": raw["valuation_date"].map(fmt_date),
            "剩余期限": raw["remaining_years"].map(fmt_float),
            "修正久期": raw["modified_duration"].map(fmt_float),
            "估值收益率": raw["valuation_yield_pct"].map(fmt_float),
            "收益率分位": raw["yield_percentile_pct"].map(fmt_float),
            "近20日成交天数": pd.to_numeric(raw["trade_days_20"], errors="raise").astype(int),
        }
    )
    return result[RESULT_COLUMNS]


def main() -> pd.DataFrame:
    base_dir = Path(__file__).resolve().parent
    current = fetch_current_candidates(base_dir)
    if current.empty:
        result = pd.DataFrame(columns=RESULT_COLUMNS)
    else:
        trade_dates = fetch_recent_trade_dates()
        trade20 = fetch_trade20(current["MAINCODE"].astype(int).tolist(), trade_dates)
        current = current.merge(trade20, on="MAINCODE", how="inner")
        current = current[current["trade_days_20"] >= 15].copy()
        history = fetch_history(current["INNERCODE"].astype(int).tolist())
        percentile = percent_rank_latest(history, current)
        current = current.merge(percentile, on="INNERCODE", how="inner")
        current = current[current["yield_percentile_pct"] >= 80].copy()
        current = current.sort_values(
            ["valuation_yield_pct", "bond_code"],
            ascending=[False, True],
        )
        result = format_result(current)
    result.to_csv(base_dir / "result.csv", index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
