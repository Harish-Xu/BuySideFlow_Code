#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = "2025-12-31"
LOOKBACK_START = "2023-01-01"
RSI_PERIOD = 14
RESULT_COLUMNS = [
    "主基金代码",
    "基金简称",
    "创高日期",
    "最新净值日期",
    "最新RSI",
    "较创高回撤",
]


SQL = f"""
WITH params AS (
    SELECT
        DATE('{AS_OF_DATE}') AS as_of_date,
        DATE('{LOOKBACK_START}') AS lookback_start,
        DATE('2025-12-01') AS december_start,
        DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY) AS as_of_next_day
),
jy_type_ranked AS (
    SELECT
        jt.INNERCODE,
        jt.FIRSTASSETCATCODE,
        jt.SECASSETCATCODE,
        jt.THIRDASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY jt.INNERCODE
            ORDER BY jt.EFFECTIVEDATE DESC, jt.INSERTTIME DESC, jt.ID DESC
        ) AS rn
    FROM mf_jyfundtype jt
    JOIN params p
      ON 1 = 1
    WHERE jt.STANDARD = 75
      AND jt.EFFECTIVEDATE <= p.as_of_date
      AND (jt.CANCELDATE IS NULL OR jt.CANCELDATE > p.as_of_date)
      AND jt.INSERTTIME < p.as_of_next_day
      AND jt.UPDATETIME < p.as_of_next_day
),
fund_pool AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        COALESCE(sm.SECUABBR, fa.SECURITYCODE) AS fund_name
    FROM mf_fundarchives fa
    JOIN jy_type_ranked jt
      ON jt.INNERCODE = fa.INNERCODE
     AND jt.rn = 1
    LEFT JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN params p
      ON 1 = 1
    WHERE COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= DATE_SUB(p.as_of_date, INTERVAL 1 YEAR)
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.as_of_date)
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND fa.INVESTMENTTYPE = 16
      AND jt.FIRSTASSETCATCODE = 11
      AND jt.SECASSETCATCODE <> 1102
      AND jt.THIRDASSETCATCODE NOT IN (110201, 110202)
),
nav_ranked AS (
    SELECT
        fp.INNERCODE,
        fp.fund_code,
        fp.fund_name,
        DATE(n.TRADINGDAY) AS nav_date,
        n.UNITNVRESTORED AS adj_nav,
        ROW_NUMBER() OVER (
            PARTITION BY fp.INNERCODE, DATE(n.TRADINGDAY)
            ORDER BY n.INSERTTIME DESC, n.UPDATETIME DESC, n.ID DESC
        ) AS rn
    FROM fund_pool fp
    JOIN mf_fundnetvalueretrans n
      ON n.INNERCODE = fp.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE n.TRADINGDAY >= p.lookback_start
      AND n.TRADINGDAY <= p.as_of_date
      AND n.UNITNVRESTORED IS NOT NULL
      AND n.UNITNVRESTORED > 0
      AND n.INSERTTIME < p.as_of_next_day
      AND n.UPDATETIME < p.as_of_next_day
),
nav_dedup AS (
    SELECT
        INNERCODE,
        fund_code,
        fund_name,
        nav_date,
        adj_nav
    FROM nav_ranked
    WHERE rn = 1
),
fund_maxima AS (
    SELECT
        n.INNERCODE,
        MAX(CASE WHEN n.nav_date < p.december_start THEN n.adj_nav END) AS pre_december_max,
        MAX(
            CASE
                WHEN n.nav_date >= p.december_start AND n.nav_date <= p.as_of_date
                THEN n.adj_nav
            END
        ) AS december_max
    FROM nav_dedup n
    JOIN params p
      ON 1 = 1
    GROUP BY n.INNERCODE
),
candidate_funds AS (
    SELECT INNERCODE
    FROM fund_maxima
    WHERE december_max IS NOT NULL
      AND (pre_december_max IS NULL OR december_max > pre_december_max)
)
SELECT
    n.INNERCODE,
    n.fund_code,
    n.fund_name,
    JSON_ARRAYAGG(
        JSON_ARRAY(DATE_FORMAT(n.nav_date, '%Y-%m-%d'), n.adj_nav)
    ) AS nav_history
FROM nav_dedup n
JOIN candidate_funds c
  ON c.INNERCODE = n.INNERCODE
GROUP BY n.INNERCODE, n.fund_code, n.fund_name
ORDER BY n.fund_code
"""


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def wilder_rsi(nav: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    values = pd.to_numeric(nav, errors="coerce").to_numpy(dtype=float)
    out = np.full(len(values), np.nan, dtype=float)
    if len(values) <= period:
        return pd.Series(out, index=nav.index)

    delta = np.diff(values, prepend=np.nan)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)

    avg_gain = np.nanmean(gains[1 : period + 1])
    avg_loss = np.nanmean(losses[1 : period + 1])

    for i in range(period, len(values)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0 and avg_gain == 0:
            out[i] = 50.0
        elif avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - 100.0 / (1.0 + rs)

    return pd.Series(out, index=nav.index)


def prepare_nav() -> pd.DataFrame:
    aggregated = query_to_dataframe(SQL, max_rows=0, timeout=900)
    if aggregated.empty:
        return aggregated

    nav_parts = []
    for fund in aggregated.itertuples(index=False):
        history = json.loads(fund.nav_history) if isinstance(fund.nav_history, str) else fund.nav_history
        part = pd.DataFrame(history, columns=["nav_date", "adj_nav"])
        part.insert(0, "fund_name", fund.fund_name)
        part.insert(0, "fund_code", fund.fund_code)
        part.insert(0, "INNERCODE", fund.INNERCODE)
        nav_parts.append(part)

    df = pd.concat(nav_parts, ignore_index=True)
    df["INNERCODE"] = pd.to_numeric(df["INNERCODE"], errors="coerce")
    df["nav_date"] = pd.to_datetime(df["nav_date"], errors="coerce")
    df["adj_nav"] = pd.to_numeric(df["adj_nav"], errors="coerce")
    df = df.dropna(subset=["INNERCODE", "fund_code", "fund_name", "nav_date", "adj_nav"]).copy()
    df["INNERCODE"] = df["INNERCODE"].astype("int64")
    return df.sort_values(["INNERCODE", "nav_date"], kind="stable").reset_index(drop=True)


def select_funds(nav: pd.DataFrame) -> pd.DataFrame:
    if nav.empty:
        return empty_result()

    df = nav.copy()
    df["prev_max_nav"] = df.groupby("INNERCODE", sort=False)["adj_nav"].cummax().shift(1)
    first_row = df.groupby("INNERCODE", sort=False).cumcount() == 0
    df.loc[first_row, "prev_max_nav"] = np.nan
    df["is_new_high"] = df["prev_max_nav"].isna() | (df["adj_nav"] > df["prev_max_nav"])

    december_start = pd.Timestamp("2025-12-01")
    as_of = pd.Timestamp(AS_OF_DATE)
    high_hits = df[
        (df["nav_date"] >= december_start)
        & (df["nav_date"] <= as_of)
        & df["is_new_high"]
    ].copy()
    if high_hits.empty:
        return empty_result()

    high_hits = high_hits.sort_values(["INNERCODE", "nav_date"], kind="stable")
    high_info = (
        high_hits.groupby("INNERCODE", sort=False)
        .tail(1)[["INNERCODE", "nav_date", "adj_nav"]]
        .rename(columns={"nav_date": "high_date", "adj_nav": "high_nav"})
    )

    df["rsi_14"] = df.groupby("INNERCODE", sort=False)["adj_nav"].transform(wilder_rsi)
    latest_info = (
        df.groupby("INNERCODE", sort=False)
        .tail(1)[["INNERCODE", "fund_code", "fund_name", "nav_date", "adj_nav", "rsi_14"]]
        .rename(columns={"nav_date": "latest_nav_date", "adj_nav": "latest_nav"})
    )

    selected = latest_info.merge(high_info, on="INNERCODE", how="inner")
    selected = selected[selected["rsi_14"] < 50].copy()
    if selected.empty:
        return empty_result()

    selected["drawdown_pct"] = (1.0 - selected["latest_nav"] / selected["high_nav"]) * 100.0
    result = pd.DataFrame(
        {
            "主基金代码": selected["fund_code"].astype(str),
            "基金简称": selected["fund_name"].astype(str),
            "创高日期": selected["high_date"].dt.strftime("%Y-%m-%d"),
            "最新净值日期": selected["latest_nav_date"].dt.strftime("%Y-%m-%d"),
            "最新RSI": selected["rsi_14"].map(lambda x: f"{float(x):.2f}"),
            "较创高回撤": selected["drawdown_pct"].map(lambda x: f"{float(x):.2f}"),
        }
    )
    result = result.sort_values(["创高日期", "主基金代码"], ascending=[False, True]).reset_index(drop=True)
    return result[RESULT_COLUMNS]


def main() -> pd.DataFrame:
    result = select_funds(prepare_nav())
    output_path = Path(__file__).with_name("result.csv")
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    __result_obj = main()
