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


AS_OF_DATE = pd.Timestamp("2025-12-31")
RECENT_MONTHS = 13
HOLDING_DAYS = 20
TARGET_INDUSTRIES = {"801030", "801040", "801050", "801890"}
BENCHMARK_CODE = "000300"


def compound_return(series: pd.Series) -> float:
    valid = series.dropna()
    if len(valid) < HOLDING_DAYS:
        return np.nan
    return float((1 + valid.iloc[:HOLDING_DAYS]).prod() - 1)


def identify_repair_months(pmi_df: pd.DataFrame) -> pd.DataFrame:
    pmi = pmi_df.sort_values("data_date").copy()
    pmi["month"] = pmi["data_date"].dt.to_period("M")
    pmi["pmi_3m_avg"] = pmi["data_value"].rolling(3, min_periods=3).mean()
    pmi["prev_3m_avg"] = pmi["data_value"].shift(3).rolling(3, min_periods=3).mean()
    pmi["pmi_3m_avg_change"] = pmi["pmi_3m_avg"] - pmi["prev_3m_avg"]
    yoy_map = pmi.set_index("month")["data_value"].to_dict()
    pmi["pmi_yoy_change"] = pmi.apply(
        lambda row: row["data_value"] - yoy_map.get(row["month"] - 12, np.nan),
        axis=1,
    )
    recent_months = pmi[pmi["data_date"] <= AS_OF_DATE].tail(RECENT_MONTHS)
    repair = recent_months[
        (recent_months["pmi_3m_avg_change"] > 0)
        & (recent_months["pmi_yoy_change"] > 0)
    ].copy()
    return repair


def future_return(
    quote_df: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    code: str,
    publish_date: pd.Timestamp,
) -> float:
    window = calendar[calendar > publish_date.normalize()][:HOLDING_DAYS]
    if len(window) < HOLDING_DAYS:
        return np.nan
    q = (
        quote_df[quote_df["instrument_code"] == code]
        .set_index("data_date")
        .reindex(window)
    )
    if q["daily_return"].isna().any():
        return np.nan
    return compound_return(q["daily_return"])


def build_result(raw_df: pd.DataFrame) -> pd.DataFrame:
    if raw_df.empty:
        raise ValueError("No data returned")

    df = raw_df.copy()
    df["data_date"] = pd.to_datetime(df["data_date"])
    df["info_publish_date"] = pd.to_datetime(df["info_publish_date"])
    pmi_df = df[df["data_type"] == "pmi"].copy()
    calendar = pd.DatetimeIndex(
        df.loc[df["data_type"] == "calendar", "data_date"]
        .dropna()
        .sort_values()
        .drop_duplicates()
    )
    quote_df = df[df["data_type"] == "quote"].copy()
    if pmi_df.empty or quote_df.empty or calendar.empty:
        raise ValueError("Missing PMI, calendar, or quote data")

    repair_months = identify_repair_months(pmi_df)
    rows: list[dict[str, object]] = []
    for _, repair in repair_months.iterrows():
        publish_date = repair["info_publish_date"]
        benchmark_ret = future_return(
            quote_df, calendar, BENCHMARK_CODE, publish_date
        )
        if pd.isna(benchmark_ret):
            continue
        for code in sorted(TARGET_INDUSTRIES):
            industry_quote = quote_df[quote_df["instrument_code"] == code]
            if industry_quote.empty:
                continue
            industry_name = industry_quote["instrument_name"].iloc[0]
            industry_ret = future_return(quote_df, calendar, code, publish_date)
            if pd.isna(industry_ret):
                continue
            rows.append(
                {
                    "信息发布日期": publish_date.strftime("%Y-%m-%d"),
                    "最新PMI": repair["data_value"],
                    "PMI近3月均值变化": repair["pmi_3m_avg_change"],
                    "PMI同比变化": repair["pmi_yoy_change"],
                    "行业名称": industry_name,
                    "后20日行业超额收益": industry_ret - benchmark_ret,
                }
            )

    result = pd.DataFrame(rows)
    if result.empty:
        raise ValueError("No repair-month industry return rows generated")
    numeric_cols = ["最新PMI", "PMI近3月均值变化", "PMI同比变化", "后20日行业超额收益"]
    result[numeric_cols] = result[numeric_cols].round(6)
    return result.sort_values("后20日行业超额收益", ascending=False).reset_index(drop=True)


def main() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    raw_df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if raw_df is None:
        raise ValueError("SQL execution returned None")
    result = build_result(raw_df)
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    main()
