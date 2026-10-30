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


START_DATE = "2020-01-01"
END_DATE = "2025-12-31"
ROLLING_WINDOW = 60
MIN_VALID_OBS = 50


INDEX_SQL = f"""
WITH index_info AS (
    SELECT INNERCODE, SECUCODE
    FROM (
        SELECT
            sm.INNERCODE,
            sm.SECUCODE,
            ROW_NUMBER() OVER (
                PARTITION BY sm.SECUCODE
                ORDER BY sm.INNERCODE
            ) AS rn
        FROM secumain sm
        WHERE sm.SECUCATEGORY = 4
          AND sm.SECUCODE IN ('399303', '000016')
    ) t
    WHERE rn = 1
)
SELECT
    DATE(q.TRADINGDAY) AS trade_date,
    ii.SECUCODE AS index_code,
    q.CLOSEPRICE AS close_price
FROM index_info ii
JOIN qt_indexquote q
  ON q.INNERCODE = ii.INNERCODE
WHERE q.TRADINGDAY >= DATE('{START_DATE}')
  AND q.TRADINGDAY <= DATE('{END_DATE}')
  AND q.CLOSEPRICE > 0
ORDER BY q.TRADINGDAY, ii.SECUCODE
"""


UST10Y_SQL = f"""
SELECT
    DATE(ENDDATE) AS yield_date,
    YIELDRATE AS ust10y_yield
FROM (
    SELECT
        y.ENDDATE,
        y.YIELDRATE,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(y.ENDDATE)
            ORDER BY y.UPDATETIME DESC, y.JSID DESC, y.ID DESC
        ) AS rn
    FROM bond_sapyieldcurve y
    WHERE y.CURVECODE = 188
      AND y.YIELDTYPECODE = 1
      AND ABS(y.REMAINMATURITY - 10) < 0.0001
      AND y.ENDDATE >= DATE_SUB(DATE('{START_DATE}'), INTERVAL 30 DAY)
      AND y.ENDDATE < DATE('{END_DATE}')
      AND y.YIELDRATE IS NOT NULL
) t
WHERE rn = 1
ORDER BY yield_date
"""


def load_style_ratio() -> pd.DataFrame:
    quotes = query_to_dataframe(INDEX_SQL, max_rows=0, timeout=180)
    if quotes is None or quotes.empty:
        raise ValueError("No index quote data returned")

    quotes["trade_date"] = pd.to_datetime(quotes["trade_date"])
    quotes["close_price"] = pd.to_numeric(quotes["close_price"], errors="coerce")
    pivot = quotes.pivot_table(
        index="trade_date",
        columns="index_code",
        values="close_price",
        aggfunc="last",
    ).sort_index()

    required = {"399303", "000016"}
    missing = required.difference(set(map(str, pivot.columns)))
    if missing:
        raise ValueError(f"Missing index quote series: {sorted(missing)}")

    style = pivot.rename(columns={"399303": "gz2000_close", "000016": "sse50_close"})
    style = style.dropna(subset=["gz2000_close", "sse50_close"]).copy()
    style["small_large_ratio"] = style["gz2000_close"] / style["sse50_close"]
    return style.reset_index()


def load_ust10y() -> pd.DataFrame:
    ust = query_to_dataframe(UST10Y_SQL, max_rows=0, timeout=180)
    if ust is None or ust.empty:
        raise ValueError("No UST 10Y yield data returned")

    ust["yield_date"] = pd.to_datetime(ust["yield_date"])
    ust["ust10y_yield"] = pd.to_numeric(ust["ust10y_yield"], errors="coerce")
    return (
        ust.dropna(subset=["yield_date", "ust10y_yield"])
        .sort_values("yield_date")
        .drop_duplicates("yield_date", keep="last")
    )


def build_analysis_frame() -> pd.DataFrame:
    style = load_style_ratio().sort_values("trade_date")
    ust = load_ust10y().sort_values("yield_date")

    analysis = pd.merge_asof(
        style,
        ust,
        left_on="trade_date",
        right_on="yield_date",
        direction="backward",
        allow_exact_matches=False,
    )
    analysis = analysis.sort_values("trade_date").reset_index(drop=True)
    analysis["style_relative_return"] = np.log(analysis["small_large_ratio"]).diff()
    analysis["ust10y_change"] = analysis["ust10y_yield"].diff()
    analysis["rolling_corr"] = (
        analysis["style_relative_return"]
        .rolling(ROLLING_WINDOW, min_periods=MIN_VALID_OBS)
        .corr(analysis["ust10y_change"])
    )
    return analysis


def summarize(analysis: pd.DataFrame) -> pd.DataFrame:
    valid = analysis.dropna(subset=["rolling_corr"]).copy()
    if valid.empty:
        raise ValueError("No valid rolling correlation windows")

    max_row = valid.loc[valid["rolling_corr"].idxmax()]
    min_row = valid.loc[valid["rolling_corr"].idxmin()]
    last_row = valid.iloc[-1]

    return pd.DataFrame(
        [
            {
                "有效窗口数": int(len(valid)),
                "平均滚动相关": float(valid["rolling_corr"].mean()),
                "最大滚动相关": float(max_row["rolling_corr"]),
                "最大值日期": max_row["trade_date"].date().isoformat(),
                "最小滚动相关": float(min_row["rolling_corr"]),
                "最小值日期": min_row["trade_date"].date().isoformat(),
                "最后计算日": last_row["trade_date"].date().isoformat(),
                "最后滚动相关": float(last_row["rolling_corr"]),
            }
        ]
    )


def main() -> pd.DataFrame:
    output_path = Path(__file__).with_name("result.csv")
    result = summarize(build_analysis_frame())
    result.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.4f")
    print(result.to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    return result


if __name__ == "__main__":
    main()
