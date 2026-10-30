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

from sweagent.text2sql.db_connector import query_to_dataframe, shared_connection_session


AS_OF_DATE = "2025-12-31"
NEXT_TS = "2026-01-01 00:00:00"
CHUNK_SIZE = 900
EXIT_CHUNK_SIZE = 200
RESULT_COLUMNS = [
    "分组",
    "样本数",
    "不破发占比(%)",
    "平均首日价差(元)",
    "前5日可退出占比(%)",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="2025 年信用债发行放量/缩量首日估值和退出表现")
    parser.add_argument("--output-path", default=str(Path(__file__).with_name("result.csv")))
    return parser.parse_args()


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def chunks(frame: pd.DataFrame, size: int = CHUNK_SIZE):
    for start in range(0, len(frame), size):
        yield frame.iloc[start : start + size]


def candidate_cte_with_innercode(frame: pd.DataFrame) -> str:
    rows = []
    columns = ["MAINCODE", "innercode", "listed_market", "listed_date"]
    for row in frame[columns].drop_duplicates("MAINCODE").itertuples(index=False):
        listed = pd.Timestamp(row.listed_date).date().isoformat()
        rows.append(
            f"SELECT {int(row.MAINCODE)} AS MAINCODE, "
            f"{int(row.innercode)} AS INNERCODE, DATE('{listed}') AS listed_date"
            f", {int(row.listed_market)} AS listed_market"
        )
    return "\nUNION ALL\n".join(rows)


def load_candidates() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")
    if df.empty:
        return df

    df = df.copy()
    for col in [
        "MAINCODE",
        "innercode",
        "listed_market",
        "plan_issue_size_mn",
        "actual_issue_size_mn",
        "issue_size_ratio",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["issue_date", "listed_date"]:
        df[col] = pd.to_datetime(df[col], errors="coerce")
    df = df.dropna(
        subset=[
            "MAINCODE",
            "innercode",
            "listed_market",
            "bond_code",
            "bond_name",
            "issue_date",
            "listed_date",
            "plan_issue_size_mn",
            "actual_issue_size_mn",
            "issue_size_ratio",
        ]
    ).copy()
    df["MAINCODE"] = df["MAINCODE"].astype("int64")
    df["innercode"] = df["innercode"].astype("int64")
    df["listed_market"] = df["listed_market"].astype("int64")
    return df.reset_index(drop=True)


def fetch_first_recommended_valuation(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    for part in chunks(candidates):
        sql = f"""
        WITH candidate AS (
            {candidate_cte_with_innercode(part)}
        ),
        valuation_base AS (
            SELECT
                c.MAINCODE,
                DATE(v.ENDDATE) AS valuation_date,
                v.VALUECLEANPRICE AS clean_price,
                v.YIELDCODE,
                v.ID,
                v.UPDATETIME,
                v.INSERTTIME
            FROM candidate c
            JOIN bond_cbvaluationall v
              ON v.INNERCODE = c.INNERCODE
            WHERE v.ENDDATE >= c.listed_date
              AND v.ENDDATE <= DATE('{AS_OF_DATE}')
              AND v.CREDIBILITYCODE = 1
              AND v.VALUECLEANPRICE IS NOT NULL
              AND v.VALUECLEANPRICE > 0
              AND (v.INSERTTIME IS NULL OR v.INSERTTIME < TIMESTAMP('{NEXT_TS}'))
        ),
        first_date AS (
            SELECT
                MAINCODE,
                MIN(valuation_date) AS first_valuation_date
            FROM valuation_base
            GROUP BY MAINCODE
        ),
        valuation_ranked AS (
            SELECT
                vb.MAINCODE,
                fd.first_valuation_date,
                vb.clean_price,
                vb.YIELDCODE,
                ROW_NUMBER() OVER (
                    PARTITION BY vb.MAINCODE
                    ORDER BY
                        CASE WHEN vb.YIELDCODE = 2 THEN 0 WHEN vb.YIELDCODE IS NULL THEN 1 ELSE 2 END,
                        vb.UPDATETIME DESC,
                        vb.INSERTTIME DESC,
                        vb.ID DESC
                ) AS rn
            FROM valuation_base vb
            JOIN first_date fd
              ON fd.MAINCODE = vb.MAINCODE
             AND fd.first_valuation_date = vb.valuation_date
        )
        SELECT
            MAINCODE,
            first_valuation_date,
            clean_price AS first_clean_price,
            YIELDCODE AS valuation_yieldcode
        FROM valuation_ranked
        WHERE rn = 1;
        """
        frames.append(query_to_dataframe(sql, max_rows=0, timeout=600))

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if df.empty:
        return df
    df = df.copy()
    df["MAINCODE"] = pd.to_numeric(df["MAINCODE"], errors="coerce")
    df["first_valuation_date"] = pd.to_datetime(df["first_valuation_date"], errors="coerce")
    df["first_clean_price"] = pd.to_numeric(df["first_clean_price"], errors="coerce")
    df["valuation_yieldcode"] = pd.to_numeric(df["valuation_yieldcode"], errors="coerce")
    df = df.dropna(subset=["MAINCODE", "first_valuation_date", "first_clean_price"]).copy()
    df["MAINCODE"] = df["MAINCODE"].astype("int64")
    return df.drop_duplicates("MAINCODE").reset_index(drop=True)


def fetch_exit_flags(candidates: pd.DataFrame) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    for part in chunks(candidates, EXIT_CHUNK_SIZE):
        sql = f"""
        WITH candidate AS (
            {candidate_cte_with_innercode(part)}
        ),
        calendar_days AS (
            SELECT
                c.MAINCODE,
                DATE(td.TRADINGDATE) AS trade_date,
                ROW_NUMBER() OVER (
                    PARTITION BY c.MAINCODE
                    ORDER BY td.TRADINGDATE
                ) AS trade_rank
            FROM candidate c
            JOIN qt_tradingdaynew td
              ON td.SECUMARKET = c.listed_market
             AND td.IFTRADINGDAY = 1
             AND td.TRADINGDATE >= c.listed_date
             AND td.TRADINGDATE <= DATE('{AS_OF_DATE}')
        ),
        first_five_calendar_days AS (
            SELECT MAINCODE, trade_date, trade_rank
            FROM calendar_days
            WHERE trade_rank <= 5
        ),
        daily_quote AS (
            SELECT
                c.MAINCODE,
                DATE(q.TRADINGDAY) AS trade_date,
                CASE
                    WHEN q.IFTRADING = 1
                     AND (
                         COALESCE(q.TURNOVERVALUE, 0) > 0
                         OR COALESCE(q.TURNOVERVOLUME, 0) > 0
                         OR COALESCE(q.TURNOVERDEALS, 0) > 0
                     )
                    THEN 1 ELSE 0
                END AS has_trade,
                ROW_NUMBER() OVER (
                    PARTITION BY c.MAINCODE, DATE(q.TRADINGDAY)
                    ORDER BY q.ID DESC
                ) AS quote_rn
            FROM candidate c
            JOIN bond_basicquote q
              ON q.INNERCODE = c.INNERCODE
            WHERE q.TRADINGDAY >= c.listed_date
              AND q.TRADINGDAY <= LEAST(DATE_ADD(c.listed_date, INTERVAL 45 DAY), DATE('{AS_OF_DATE}'))
              AND (q.INSERTTIME IS NULL OR q.INSERTTIME < TIMESTAMP('{NEXT_TS}'))
        ),
        quote_one AS (
            SELECT
                MAINCODE,
                trade_date,
                has_trade
            FROM daily_quote
            WHERE quote_rn = 1
        )
        SELECT
            cd.MAINCODE,
            COUNT(*) AS available_trade_days_to_cutoff,
            MAX(CASE WHEN COALESCE(q.has_trade, 0) = 1 THEN 1 ELSE 0 END) AS exit_available
        FROM first_five_calendar_days cd
        LEFT JOIN quote_one q
          ON q.MAINCODE = cd.MAINCODE
         AND q.trade_date = cd.trade_date
        GROUP BY cd.MAINCODE;
        """
        frames.append(query_to_dataframe(sql, max_rows=0, timeout=600))

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if df.empty:
        return df
    df = df.copy()
    for col in ["MAINCODE", "available_trade_days_to_cutoff", "exit_available"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["MAINCODE"]).copy()
    df["MAINCODE"] = df["MAINCODE"].astype("int64")
    df["available_trade_days_to_cutoff"] = df["available_trade_days_to_cutoff"].fillna(0).astype(int)
    df["exit_available"] = df["exit_available"].fillna(0).astype(int)
    return df.drop_duplicates("MAINCODE").reset_index(drop=True)


def build_analysis_dataset(
    candidates: pd.DataFrame,
    valuations: pd.DataFrame,
    exit_flags: pd.DataFrame,
) -> pd.DataFrame:
    if candidates.empty or valuations.empty:
        return pd.DataFrame()

    analysis = candidates.merge(valuations, on="MAINCODE", how="inner")
    analysis["first_day_spread"] = analysis["first_clean_price"] - 100.0
    analysis["not_broken"] = analysis["first_clean_price"] >= 100.0

    if not exit_flags.empty:
        analysis = analysis.merge(exit_flags, on="MAINCODE", how="left")
    else:
        analysis["available_trade_days_to_cutoff"] = np.nan
        analysis["exit_available"] = np.nan
    analysis["exit_metric_eligible"] = analysis["available_trade_days_to_cutoff"] >= 5
    return analysis.reset_index(drop=True)


def summarize_one(label: str, frame: pd.DataFrame) -> dict[str, object]:
    if frame.empty:
        return {
            "分组": label,
            "样本数": 0,
            "不破发占比(%)": np.nan,
            "平均首日价差(元)": np.nan,
            "前5日可退出占比(%)": np.nan,
        }

    exit_base = frame[frame["exit_metric_eligible"]].copy()
    return {
        "分组": label,
        "样本数": int(len(frame)),
        "不破发占比(%)": float(frame["not_broken"].mean() * 100.0),
        "平均首日价差(元)": float(frame["first_day_spread"].mean()),
        "前5日可退出占比(%)": (
            float(exit_base["exit_available"].mean() * 100.0) if not exit_base.empty else np.nan
        ),
    }


def summarize(analysis: pd.DataFrame) -> pd.DataFrame:
    if analysis.empty:
        return empty_result()

    rows = [
        summarize_one("放量组", analysis[analysis["issue_size_ratio"] >= 1.1]),
        summarize_one("缩量组", analysis[analysis["issue_size_ratio"] <= 0.9]),
        summarize_one("全部基线", analysis),
    ]
    return pd.DataFrame(rows)[RESULT_COLUMNS]


def format_result(result: pd.DataFrame) -> pd.DataFrame:
    if result.empty:
        return empty_result()

    formatted = result.copy()
    formatted["样本数"] = pd.to_numeric(formatted["样本数"], errors="coerce").fillna(0).astype(int)
    for col in ["不破发占比(%)", "平均首日价差(元)", "前5日可退出占比(%)"]:
        formatted[col] = pd.to_numeric(formatted[col], errors="coerce").map(
            lambda value: f"{value:.2f}" if pd.notna(value) else ""
        )
    return formatted[RESULT_COLUMNS]


def main() -> pd.DataFrame:
    with shared_connection_session():
        candidates = load_candidates()
        if candidates.empty:
            return empty_result()

        valuations = fetch_first_recommended_valuation(candidates)
        exit_flags = fetch_exit_flags(candidates)
    analysis = build_analysis_dataset(candidates, valuations, exit_flags)
    return format_result(summarize(analysis))


if __name__ == "__main__":
    args = parse_args()
    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = main()
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
