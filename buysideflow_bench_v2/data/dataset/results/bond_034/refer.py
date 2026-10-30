#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


SHORT_INDEX_CODE = "CBA00621"
LONG_INDEX_CODE = "CBA00651"
WINDOW_DAYS = 20

RESULT_COLUMNS = [
    "倒挂月数",
    "可用样本数",
    "1-3年指数平均收益(%)",
    "7-10年指数平均收益(%)",
    "短久期减长久期平均收益差(%)",
    "短久期占优胜率(%)",
]

FR_SQL = """
SELECT
    DATE(ENDDATE) AS trade_date,
    FR007 AS fr007_pct,
    UPDATETIME AS record_update_time,
    ID AS record_id
FROM bond_baseratereference FORCE INDEX (IX_BOND_BASERATEREFERENCE)
WHERE ENDDATE >= '2022-01-01'
  AND ENDDATE < '2025-01-01'
  AND FR007 IS NOT NULL
"""

CURVE_SQL = """
SELECT
    DATE(ENDDATE) AS trade_date,
    YIELD * 100 AS treasury_3y_pct,
    UPDATETIME AS record_update_time,
    ID AS record_id
FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
WHERE ENDDATE >= '2022-01-01'
  AND ENDDATE < '2025-01-01'
  AND CURVECODE = 10
  AND YIELDTYPECODE = 1
  AND YEARSTOMATURITY = 3
  AND YIELD IS NOT NULL
"""

INDEX_QUOTE_SQL = """
SELECT
    CASE
        WHEN INNERCODE = 3959 THEN 'CBA00621'
        WHEN INNERCODE = 3962 THEN 'CBA00651'
    END AS index_code,
    DATE(TRADINGDAY) AS trade_date,
    CLOSEPRICE AS close_price,
    UPDATETIME AS record_update_time,
    ID AS record_id
FROM bond_chinabondindexquote
WHERE TRADINGDAY >= '2022-02-01'
  AND TRADINGDAY < '2025-04-01'
  AND INNERCODE IN (3959, 3962)
  AND CLOSEPRICE IS NOT NULL
  AND CLOSEPRICE > 0
"""


def load_raw_data() -> pd.DataFrame:
    fr = query_to_dataframe(FR_SQL, max_rows=0, timeout=60)
    curve = query_to_dataframe(CURVE_SQL, max_rows=0, timeout=60)
    quotes = query_to_dataframe(INDEX_QUOTE_SQL, max_rows=0, timeout=60)
    if fr is None or curve is None or quotes is None:
        raise ValueError("SQL execution returned None")

    fr["row_type"] = "fr_rate"
    curve["row_type"] = "curve_rate"
    quotes["row_type"] = "index_quote"
    return pd.concat([fr, curve, quotes], ignore_index=True, sort=False)


def prepare_daily_rates(raw: pd.DataFrame) -> pd.DataFrame:
    fr = raw.loc[raw["row_type"] == "fr_rate"].copy()
    curve = raw.loc[raw["row_type"] == "curve_rate"].copy()
    if fr.empty or curve.empty:
        return pd.DataFrame(columns=["trade_date", "trigger_month", "fr007_pct", "treasury_3y_pct"])

    fr["trade_date"] = pd.to_datetime(fr["trade_date"])
    fr["record_update_time"] = pd.to_datetime(fr["record_update_time"])
    fr["record_id"] = pd.to_numeric(fr["record_id"], errors="coerce")
    fr["fr007_pct"] = pd.to_numeric(fr["fr007_pct"], errors="coerce")
    fr = (
        fr[["trade_date", "fr007_pct", "record_update_time", "record_id"]]
        .dropna(subset=["trade_date", "fr007_pct", "record_update_time", "record_id"])
        .sort_values(["trade_date", "record_update_time", "record_id"])
        .drop_duplicates("trade_date", keep="last")
    )

    curve["trade_date"] = pd.to_datetime(curve["trade_date"])
    curve["record_update_time"] = pd.to_datetime(curve["record_update_time"])
    curve["record_id"] = pd.to_numeric(curve["record_id"], errors="coerce")
    curve["treasury_3y_pct"] = pd.to_numeric(curve["treasury_3y_pct"], errors="coerce")
    curve = (
        curve[["trade_date", "treasury_3y_pct", "record_update_time", "record_id"]]
        .dropna(subset=["trade_date", "treasury_3y_pct", "record_update_time", "record_id"])
        .sort_values(["trade_date", "record_update_time", "record_id"])
        .drop_duplicates("trade_date", keep="last")
    )

    rates = (
        fr[["trade_date", "fr007_pct"]]
        .merge(curve[["trade_date", "treasury_3y_pct"]], on="trade_date", how="inner")
        .sort_values("trade_date")
        .reset_index(drop=True)
    )
    rates["trigger_month"] = rates["trade_date"].dt.strftime("%Y-%m")
    return (
        rates[["trade_date", "trigger_month", "fr007_pct", "treasury_3y_pct"]]
        .dropna(subset=["trade_date", "trigger_month", "fr007_pct", "treasury_3y_pct"])
        .sort_values("trade_date")
        .reset_index(drop=True)
    )


def prepare_triggers(rates: pd.DataFrame) -> pd.DataFrame:
    if rates.empty:
        return pd.DataFrame(columns=["trigger_month", "fr007_avg_pct", "treasury_3y_avg_pct", "inversion_bp"])

    monthly = (
        rates.groupby("trigger_month", as_index=False)
        .agg(fr007_avg_pct=("fr007_pct", "mean"), treasury_3y_avg_pct=("treasury_3y_pct", "mean"))
        .sort_values("trigger_month")
        .reset_index(drop=True)
    )
    monthly["inversion_bp"] = (monthly["fr007_avg_pct"] - monthly["treasury_3y_avg_pct"]) * 100.0
    return monthly.loc[monthly["fr007_avg_pct"] > monthly["treasury_3y_avg_pct"]].reset_index(drop=True)


def prepare_quotes(raw: pd.DataFrame) -> pd.DataFrame:
    quotes = raw.loc[raw["row_type"] == "index_quote"].copy()
    if quotes.empty:
        return pd.DataFrame()

    quotes["trade_date"] = pd.to_datetime(quotes["trade_date"])
    quotes["record_update_time"] = pd.to_datetime(quotes["record_update_time"])
    quotes["record_id"] = pd.to_numeric(quotes["record_id"], errors="coerce")
    quotes["close_price"] = pd.to_numeric(quotes["close_price"], errors="coerce")
    quotes = (
        quotes[["index_code", "trade_date", "close_price", "record_update_time", "record_id"]]
        .dropna(subset=["index_code", "trade_date", "close_price", "record_update_time", "record_id"])
        .loc[lambda frame: frame["index_code"].isin([SHORT_INDEX_CODE, LONG_INDEX_CODE])]
        .sort_values(["index_code", "trade_date", "record_update_time", "record_id"])
        .drop_duplicates(["index_code", "trade_date"], keep="last")
        [["index_code", "trade_date", "close_price"]]
        .reset_index(drop=True)
    )

    missing = sorted({SHORT_INDEX_CODE, LONG_INDEX_CODE} - set(quotes["index_code"].unique()))
    if missing:
        raise ValueError(f"missing index quotes for: {missing}")
    return quotes


def next_month_start(trigger_month: str) -> pd.Timestamp:
    return (pd.Period(trigger_month, freq="M") + 1).start_time.normalize()


def build_windows(triggers: pd.DataFrame, quotes: pd.DataFrame) -> pd.DataFrame:
    detail_columns = [
        "trigger_month",
        "start_date",
        "end_date",
        "short_return_pct",
        "long_return_pct",
        "diff_return_pct",
    ]
    if triggers.empty or quotes.empty:
        return pd.DataFrame(columns=detail_columns)

    px = (
        quotes.pivot_table(index="trade_date", columns="index_code", values="close_price", aggfunc="last")
        .dropna(subset=[SHORT_INDEX_CODE, LONG_INDEX_CODE])
        .sort_index()
    )
    if px.empty:
        return pd.DataFrame(columns=detail_columns)

    rows: list[dict[str, object]] = []
    trade_dates = px.index
    for _, trigger in triggers.iterrows():
        candidates = trade_dates[trade_dates >= next_month_start(str(trigger["trigger_month"]))]
        if len(candidates) < WINDOW_DAYS:
            continue

        start_date = candidates[0]
        end_date = candidates[WINDOW_DAYS - 1]
        short_return = (float(px.loc[end_date, SHORT_INDEX_CODE]) / float(px.loc[start_date, SHORT_INDEX_CODE]) - 1.0) * 100.0
        long_return = (float(px.loc[end_date, LONG_INDEX_CODE]) / float(px.loc[start_date, LONG_INDEX_CODE]) - 1.0) * 100.0
        rows.append(
            {
                "trigger_month": trigger["trigger_month"],
                "start_date": start_date,
                "end_date": end_date,
                "short_return_pct": short_return,
                "long_return_pct": long_return,
                "diff_return_pct": short_return - long_return,
            }
        )

    return pd.DataFrame(rows, columns=detail_columns)


def build_result(triggers: pd.DataFrame, windows: pd.DataFrame) -> pd.DataFrame:
    inversion_months = len(triggers)
    available_samples = len(windows)
    if available_samples == 0:
        row = {
            "倒挂月数": inversion_months,
            "可用样本数": 0,
            "1-3年指数平均收益(%)": None,
            "7-10年指数平均收益(%)": None,
            "短久期减长久期平均收益差(%)": None,
            "短久期占优胜率(%)": None,
        }
    else:
        row = {
            "倒挂月数": inversion_months,
            "可用样本数": available_samples,
            "1-3年指数平均收益(%)": round(float(windows["short_return_pct"].mean()), 2),
            "7-10年指数平均收益(%)": round(float(windows["long_return_pct"].mean()), 2),
            "短久期减长久期平均收益差(%)": round(float(windows["diff_return_pct"].mean()), 2),
            "短久期占优胜率(%)": round(float((windows["diff_return_pct"] > 0).mean() * 100.0), 2),
        }
    return pd.DataFrame([row], columns=RESULT_COLUMNS)


def main() -> None:
    raw = load_raw_data()
    rates = prepare_daily_rates(raw)
    triggers = prepare_triggers(rates)
    windows = build_windows(triggers, prepare_quotes(raw))
    result = build_result(triggers, windows)
    result.to_csv(Path(__file__).with_name("result.csv"), index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
