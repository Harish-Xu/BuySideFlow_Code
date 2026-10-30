#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = "2025-12-31"
HOLDING_VALUATION_DAYS = 20
CHUNK_SIZE = 500

RESULT_COLUMNS = [
    "撤销违约年份",
    "样本数",
    "平均全价变化(%)",
    "全价发生变化占比",
]


def sql_list(values) -> str:
    return ",".join(str(int(v)) for v in values)


def chunks(values, size=CHUNK_SIZE):
    values = list(values)
    for i in range(0, len(values), size):
        yield values[i : i + size]


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def fetch_revoke_events() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    for col in ["maincode", "innercode"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["default_date", "revoke_date"]:
        df[col] = pd.to_datetime(df[col], errors="coerce")

    df = df.dropna(subset=["innercode", "revoke_date"]).copy()
    df["maincode"] = df["maincode"].astype("Int64")
    df["innercode"] = df["innercode"].astype("int64")
    return df.sort_values(["revoke_date", "bond_code"]).reset_index(drop=True)


def fetch_valuation_history(events: pd.DataFrame, as_of_date: str = AS_OF_DATE) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame()

    start = events["revoke_date"].min().strftime("%Y-%m-%d")
    natural_end = events["revoke_date"].max() + pd.Timedelta(days=365)
    end_ts = min(pd.Timestamp(as_of_date), natural_end)
    if pd.Timestamp(start) > end_ts:
        return pd.DataFrame()
    end = end_ts.strftime("%Y-%m-%d")
    frames: list[pd.DataFrame] = []
    for part in chunks(events["innercode"].unique()):
        sql = f"""
        SELECT
            INNERCODE,
            DATE(ENDDATE) AS valuation_date,
            VALUEFULLPRICE AS value_full_price,
            INSERTTIME,
            UPDATETIME,
            ID
        FROM bond_cbvaluationall FORCE INDEX (IX_BOND_CBVALUATIONALL)
        WHERE INNERCODE IN ({sql_list(part)})
          AND ENDDATE >= DATE('{start}')
          AND ENDDATE < DATE_ADD(DATE('{end}'), INTERVAL 1 DAY)
          AND CREDIBILITYCODE = 1
          AND VALUEFULLPRICE IS NOT NULL
          AND (INSERTTIME IS NULL OR INSERTTIME < DATE_ADD(DATE(ENDDATE), INTERVAL 1 DAY))
          AND UPDATETIME < DATE_ADD(DATE(ENDDATE), INTERVAL 1 DAY)
        """
        frames.append(query_to_dataframe(sql, max_rows=0, timeout=600))

    history = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if history.empty:
        return history

    history = history.copy()
    history["INNERCODE"] = pd.to_numeric(history["INNERCODE"], errors="coerce")
    history["valuation_date"] = pd.to_datetime(history["valuation_date"], errors="coerce")
    history["value_full_price"] = pd.to_numeric(history["value_full_price"], errors="coerce")
    history["INSERTTIME"] = pd.to_datetime(history["INSERTTIME"], errors="coerce")
    history["UPDATETIME"] = pd.to_datetime(history["UPDATETIME"], errors="coerce")
    history["ID"] = pd.to_numeric(history["ID"], errors="coerce")

    history = history.dropna(
        subset=["INNERCODE", "valuation_date", "value_full_price"]
    ).copy()
    if history.empty:
        return history

    history["INNERCODE"] = history["INNERCODE"].astype("int64")
    history = history.sort_values(
        [
            "INNERCODE",
            "valuation_date",
            "INSERTTIME",
            "UPDATETIME",
            "ID",
        ],
        ascending=[True, True, False, False, False],
    )
    return (
        history.drop_duplicates(["INNERCODE", "valuation_date"], keep="first")
        .sort_values(["INNERCODE", "valuation_date"])
        .reset_index(drop=True)
    )


def build_event_outcomes(events: pd.DataFrame, history: pd.DataFrame) -> pd.DataFrame:
    if events.empty or history.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    for _, event in events.iterrows():
        innercode = int(event["innercode"])
        revoke_date = pd.Timestamp(event["revoke_date"]).normalize()
        bond_history = history[
            (history["INNERCODE"] == innercode)
            & (history["valuation_date"] > revoke_date)
        ].sort_values("valuation_date").reset_index(drop=True)
        if bond_history.empty:
            continue

        entry = bond_history.iloc[0]
        exit_idx = HOLDING_VALUATION_DAYS
        if exit_idx >= len(bond_history):
            continue
        exit_row = bond_history.iloc[exit_idx]

        entry_price = float(entry["value_full_price"])
        exit_price = float(exit_row["value_full_price"])
        if entry_price == 0:
            continue

        full_price_change_pct = (exit_price / entry_price - 1.0) * 100.0
        rows.append(
            {
                "innercode": innercode,
                "bond_code": event["bond_code"],
                "bond_name": event["bond_name"],
                "issuer": event["issuer"],
                "revoke_year": int(revoke_date.year),
                "revoke_date": revoke_date.strftime("%Y-%m-%d"),
                "entry_valuation_date": pd.Timestamp(entry["valuation_date"]).strftime("%Y-%m-%d"),
                "exit_valuation_date": pd.Timestamp(exit_row["valuation_date"]).strftime("%Y-%m-%d"),
                "entry_full_price": entry_price,
                "exit_full_price": exit_price,
                "full_price_change_pct": full_price_change_pct,
                "full_price_changed": int(abs(full_price_change_pct) > 1e-10),
            }
        )

    return pd.DataFrame(rows)


def summarize(outcomes: pd.DataFrame, event_years: list[int]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year in sorted(event_years):
        if outcomes.empty:
            subset = pd.DataFrame()
        else:
            subset = outcomes[outcomes["revoke_year"] == year]
        sample_count = int(len(subset))
        if sample_count == 0:
            rows.append(
                {
                    "撤销违约年份": year,
                    "样本数": sample_count,
                    "平均全价变化(%)": np.nan,
                    "全价发生变化占比": np.nan,
                }
            )
            continue

        rows.append(
            {
                "撤销违约年份": year,
                "样本数": sample_count,
                "平均全价变化(%)": round(float(subset["full_price_change_pct"].mean()), 2),
                "全价发生变化占比": round(float(subset["full_price_changed"].mean() * 100.0), 2),
            }
        )

    return pd.DataFrame(rows, columns=RESULT_COLUMNS)


def format_result(result: pd.DataFrame) -> pd.DataFrame:
    if result.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    formatted = result.copy()
    int_cols = ["撤销违约年份", "样本数"]
    numeric_cols = [
        "平均全价变化(%)",
        "全价发生变化占比",
    ]
    for col in int_cols:
        formatted[col] = pd.to_numeric(formatted[col], errors="coerce").map(
            lambda value: str(int(value)) if pd.notna(value) else ""
        )
    for col in numeric_cols:
        formatted[col] = pd.to_numeric(formatted[col], errors="coerce").map(
            lambda value: f"{float(value):.2f}" if pd.notna(value) else ""
        )
    return formatted[RESULT_COLUMNS]


def main() -> pd.DataFrame:
    events = fetch_revoke_events()
    if events.empty:
        result = empty_result()
    else:
        history = fetch_valuation_history(events)
        outcomes = build_event_outcomes(events, history)
        event_years = sorted(events["revoke_date"].dt.year.dropna().astype(int).unique().tolist())
        result = summarize(outcomes, event_years)

    output_path = Path(__file__).with_name("result.csv")
    formatted = format_result(result)
    formatted.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(formatted.to_string(index=False) if not formatted.empty else "<empty result>")
    return formatted


if __name__ == "__main__":
    main()
