#!/usr/bin/env python3
"""Reference implementation for stock_156."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import get_db_connector


SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_PATH = SCRIPT_DIR / "result.csv"

HISTORY_WINDOW = 20
RETURN_LOOKBACK_DAYS = 10
FUTURE_WINDOW = 20
TOP_INDUSTRIES = 3
STOCKS_PER_INDUSTRY = 5
OUTPUT_COLUMNS = [
    "观察日",
    "行业名称",
    "行业宽度",
    "股票代码",
    "股票简称",
    "近10日收益",
    "未来20日超额收益",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build stock_156 industry pullback follow-up table.")
    parser.add_argument("--as-of-date", default="2025-12-31")
    parser.add_argument("--output-path", default=str(OUTPUT_PATH))
    return parser.parse_args()


def validate_as_of_date(as_of_date: str) -> str:
    return pd.Timestamp(as_of_date).strftime("%Y-%m-%d")


def sql_int_list(values: pd.Series | list[int]) -> str:
    unique_values = sorted(pd.Series(values).dropna().astype(int).unique().tolist())
    if not unique_values:
        raise ValueError("No integer values for SQL IN clause.")
    return ", ".join(str(value) for value in unique_values)


def sql_date_list(values: pd.Series | list[pd.Timestamp]) -> str:
    unique_values = sorted({pd.Timestamp(value).strftime("%Y-%m-%d") for value in values})
    if not unique_values:
        raise ValueError("No dates for SQL IN clause.")
    return ", ".join(f"DATE('{value}')" for value in unique_values)


def chunks(values: pd.Series | list[int], size: int = 800) -> list[list[int]]:
    unique_values = sorted(pd.Series(values).dropna().astype(int).unique().tolist())
    return [unique_values[start : start + size] for start in range(0, len(unique_values), size)]


def query(connector, sql: str, timeout: int = 900) -> pd.DataFrame:
    return connector.execute_sql_to_dataframe(sql, max_rows=0, timeout=timeout)


def fetch_context(connector, as_of_date: str) -> tuple[pd.Timestamp, pd.Timestamp, pd.DatetimeIndex, float]:
    sql = f"""
WITH
params AS (
    SELECT DATE('{as_of_date}') AS as_of_date
),
obs_day AS (
    SELECT MAX(DATE(t.TRADINGDATE)) AS obs_date
    FROM qt_tradingdaynew t
    JOIN params p
      ON DATE(t.TRADINGDATE) <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET = 83
),
past_days AS (
    SELECT trading_day, rn_desc
    FROM (
        SELECT
            DATE(t.TRADINGDATE) AS trading_day,
            ROW_NUMBER() OVER (ORDER BY DATE(t.TRADINGDATE) DESC) AS rn_desc
        FROM qt_tradingdaynew t
        JOIN obs_day od
          ON DATE(t.TRADINGDATE) <= od.obs_date
        WHERE t.IFTRADINGDAY = 1
          AND t.SECUMARKET = 83
    ) x
    WHERE rn_desc <= {HISTORY_WINDOW}
),
future_day AS (
    SELECT trading_day AS exit_date
    FROM (
        SELECT
            DATE(t.TRADINGDATE) AS trading_day,
            ROW_NUMBER() OVER (ORDER BY DATE(t.TRADINGDATE)) AS rn_asc
        FROM qt_tradingdaynew t
        JOIN obs_day od
          ON DATE(t.TRADINGDATE) > od.obs_date
        WHERE t.IFTRADINGDAY = 1
          AND t.SECUMARKET = 83
    ) x
    WHERE rn_asc = {FUTURE_WINDOW}
),
target_index AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '000985'
    ORDER BY INNERCODE
    LIMIT 1
),
benchmark_return AS (
    SELECT
        i20.CLOSEPRICE / NULLIF(i0.CLOSEPRICE, 0) - 1.0 AS benchmark_return_20d
    FROM target_index ti
    CROSS JOIN obs_day od
    CROSS JOIN future_day fd
    JOIN qt_indexquote i0
      ON i0.INNERCODE = ti.INNERCODE
     AND DATE(i0.TRADINGDAY) = od.obs_date
    JOIN qt_indexquote i20
      ON i20.INNERCODE = ti.INNERCODE
     AND DATE(i20.TRADINGDAY) = fd.exit_date
    WHERE i0.CLOSEPRICE > 0
      AND i20.CLOSEPRICE > 0
)
SELECT
    od.obs_date,
    fd.exit_date,
    pd.trading_day,
    br.benchmark_return_20d
FROM obs_day od
CROSS JOIN future_day fd
CROSS JOIN past_days pd
CROSS JOIN benchmark_return br
ORDER BY pd.trading_day;
"""
    df = query(connector, sql, timeout=180)
    if df.empty or df["trading_day"].nunique() != HISTORY_WINDOW:
        raise ValueError("Trading calendar or benchmark return is incomplete.")
    obs_date = pd.Timestamp(df["obs_date"].iloc[0])
    exit_date = pd.Timestamp(df["exit_date"].iloc[0])
    past_days = pd.DatetimeIndex(pd.to_datetime(df["trading_day"]).sort_values())
    benchmark_return = float(df["benchmark_return_20d"].iloc[0])
    return obs_date, exit_date, past_days, benchmark_return


def fetch_eligible_stocks(connector, obs_date: pd.Timestamp) -> pd.DataFrame:
    obs = obs_date.strftime("%Y-%m-%d")
    sql = f"""
WITH
params AS (
    SELECT DATE('{obs}') AS obs_date, DATE_ADD(DATE('{obs}'), INTERVAL 1 DAY) AS visible_before
),
target_index AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '000985'
    ORDER BY INNERCODE
    LIMIT 1
),
components AS (
    SELECT DISTINCT ic.SECUINNERCODE AS INNERCODE
    FROM lc_indexcomponent ic
    JOIN target_index ti
      ON ti.INNERCODE = ic.INDEXINNERCODE
    JOIN params p
      ON DATE(ic.INDATE) <= p.obs_date
    WHERE ic.OUTDATE IS NULL OR DATE(ic.OUTDATE) > p.obs_date
),
stock_base AS (
    SELECT
        sm.INNERCODE,
        sm.COMPANYCODE,
        sm.SECUCODE AS stock_code,
        COALESCE(sm.CHINAMEABBR, sm.SECUABBR, sm.CHINAME, sm.SECUCODE) AS stock_name,
        sm.SECUMARKET,
        sm.LISTEDSECTOR
    FROM components c
    JOIN secumain sm
      ON sm.INNERCODE = c.INNERCODE
    WHERE sm.SECUCATEGORY = 1
      AND sm.SECUMARKET IN (18, 83, 90)
      AND sm.COMPANYCODE IS NOT NULL
),
latest_list_status AS (
    SELECT INNERCODE
    FROM (
        SELECT
            sb.INNERCODE,
            ls.CHANGETYPE,
            ROW_NUMBER() OVER (
                PARTITION BY sb.INNERCODE
                ORDER BY DATE(ls.CHANGEDATE) DESC, ls.JSID DESC
            ) AS rn
        FROM stock_base sb
        JOIN params p
        JOIN (
            SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID, SECUMARKET
            FROM lc_liststatus
            UNION ALL
            SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID, 83 AS SECUMARKET
            FROM lc_stibliststatus
        ) ls
         ON ls.INNERCODE = sb.INNERCODE
         AND DATE(ls.CHANGEDATE) <= p.obs_date
         AND ls.SECUMARKET IN (18, 83, 90)
         AND ls.CHANGETYPE BETWEEN 1 AND 9
         AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME < p.visible_before)
    ) x
    WHERE rn = 1
      AND CHANGETYPE IN (1, 3)
),
industry_normal AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME
    FROM (
        SELECT
            sb.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
                ROW_NUMBER() OVER (
                PARTITION BY sb.COMPANYCODE
                ORDER BY DATE(e.INFOPUBLDATE) DESC,
                         e.INSERTTIME DESC,
                         e.JSID DESC
            ) AS rn
        FROM stock_base sb
        JOIN params p
        JOIN lc_exgindustry e
         ON e.COMPANYCODE = sb.COMPANYCODE
         AND DATE(e.INFOPUBLDATE) <= p.obs_date
        WHERE sb.LISTEDSECTOR <> 7
          AND e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR DATE(e.CANCELDATE) > p.obs_date)
    ) x
    WHERE rn = 1
),
industry_stib AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME
    FROM (
        SELECT
            sb.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
                ROW_NUMBER() OVER (
                PARTITION BY sb.COMPANYCODE
                ORDER BY DATE(e.EFFECTIVEDATE) DESC,
                         DATE(e.INFOPUBLDATE) DESC,
                         e.INSERTTIME DESC,
                         e.JSID DESC
            ) AS rn
        FROM stock_base sb
        JOIN params p
        JOIN lc_stibexgindustry e
         ON e.COMPANYCODE = sb.COMPANYCODE
         AND DATE(e.INFOPUBLDATE) <= p.obs_date
        WHERE sb.LISTEDSECTOR = 7
          AND e.STANDARD = 38
          AND DATE(e.EFFECTIVEDATE) <= p.obs_date
          AND (e.CANCELDATE IS NULL OR DATE(e.CANCELDATE) > p.obs_date)
    ) x
    WHERE rn = 1
),
industry_asof AS (
    SELECT * FROM industry_normal
    UNION ALL
    SELECT * FROM industry_stib
),
active_risk_alert AS (
    SELECT DISTINCT sb.INNERCODE
    FROM stock_base sb
    JOIN cs_riskalert ra
      ON ra.INNERCODE = sb.INNERCODE
    JOIN params p
      ON DATE(COALESCE(ra.IMPLANNOUCEDATE, ra.IMPLEMENTDATE)) <= p.obs_date
     AND DATE(ra.IMPLEMENTDATE) <= p.obs_date
     AND COALESCE(ra.IMPLANNOUCEDATE, ra.IMPLEMENTDATE) < p.visible_before
    WHERE ra.RISKALERTTYPE IN (1, 5, 9, 12, 14)
      AND (
          ra.REMOVEDATE IS NULL
          OR DATE(ra.REMOVEDATE) > p.obs_date
          OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.REMOVEDATE) >= p.visible_before
      )
)
SELECT
    sb.INNERCODE,
    sb.COMPANYCODE,
    sb.stock_code,
    sb.stock_name,
    sb.SECUMARKET,
    sb.LISTEDSECTOR,
    ia.FIRSTINDUSTRYNAME AS industry_name
FROM stock_base sb
LEFT JOIN latest_list_status lls
  ON lls.INNERCODE = sb.INNERCODE
JOIN industry_asof ia
  ON ia.COMPANYCODE = sb.COMPANYCODE
LEFT JOIN active_risk_alert ra
  ON ra.INNERCODE = sb.INNERCODE
WHERE ra.INNERCODE IS NULL
  AND lls.INNERCODE IS NOT NULL
ORDER BY sb.stock_code;
"""
    df = query(connector, sql, timeout=300)
    if df.empty:
        raise ValueError("No eligible constituents after PIT stock-pool filters.")
    df["INNERCODE"] = pd.to_numeric(df["INNERCODE"], errors="coerce").astype("Int64")
    df["COMPANYCODE"] = pd.to_numeric(df["COMPANYCODE"], errors="coerce").astype("Int64")
    for col in ["SECUMARKET", "LISTEDSECTOR"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    df["stock_code"] = df["stock_code"].astype(str).str.zfill(6)
    return df.dropna(subset=["INNERCODE", "industry_name"]).reset_index(drop=True)


def fetch_history_quotes(
    connector,
    stocks: pd.DataFrame,
    past_days: pd.DatetimeIndex,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    date_clause = sql_date_list(list(past_days))
    for table_name, sector_mask in [
        ("qt_dailyquote", stocks["LISTEDSECTOR"].ne(7)),
        ("lc_stibdailyquote", stocks["LISTEDSECTOR"].eq(7)),
    ]:
        innercodes = stocks.loc[sector_mask, "INNERCODE"].dropna().astype(int)
        for chunk in chunks(innercodes):
            sql = f"""
SELECT
    INNERCODE,
    DATE(TRADINGDAY) AS trading_day,
    CLOSEPRICE AS close_price
FROM {table_name}
WHERE INNERCODE IN ({sql_int_list(chunk)})
  AND TRADINGDAY IN ({date_clause})
  AND CLOSEPRICE > 0
  AND TURNOVERVALUE > 0
ORDER BY INNERCODE, TRADINGDAY;
"""
            part = query(connector, sql, timeout=300)
            if not part.empty:
                frames.append(part)
    if not frames:
        raise ValueError("No historical quotes returned.")
    quotes = pd.concat(frames, ignore_index=True)
    quotes["INNERCODE"] = pd.to_numeric(quotes["INNERCODE"], errors="coerce").astype("Int64")
    quotes["trading_day"] = pd.to_datetime(quotes["trading_day"])
    quotes["close_price"] = pd.to_numeric(quotes["close_price"], errors="coerce")
    return quotes.dropna(subset=["INNERCODE", "trading_day", "close_price"])


def fetch_return_prices(
    connector,
    stocks: pd.DataFrame,
    obs_date: pd.Timestamp,
    exit_date: pd.Timestamp,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    obs = obs_date.strftime("%Y-%m-%d")
    exit_ = exit_date.strftime("%Y-%m-%d")

    normal_codes = stocks.loc[stocks["LISTEDSECTOR"].ne(7), "INNERCODE"].dropna().astype(int)
    for chunk in chunks(normal_codes):
        sql = f"""
SELECT
    p0.INNERCODE,
    p0.BACKWARDPRICE AS obs_return_price,
    p20.BACKWARDPRICE AS exit_return_price
FROM qt_performancedata p0
JOIN qt_performancedata p20
  ON p20.INNERCODE = p0.INNERCODE
 AND DATE(p20.TRADINGDAY) = DATE('{exit_}')
WHERE p0.INNERCODE IN ({sql_int_list(chunk)})
  AND DATE(p0.TRADINGDAY) = DATE('{obs}')
  AND p0.BACKWARDPRICE > 0
  AND p20.BACKWARDPRICE > 0;
"""
        part = query(connector, sql, timeout=300)
        if not part.empty:
            frames.append(part)

    stib_codes = stocks.loc[stocks["LISTEDSECTOR"].eq(7), "INNERCODE"].dropna().astype(int)
    for chunk in chunks(stib_codes):
        sql = f"""
SELECT
    q0.INNERCODE,
    q0.CLOSEPRICE AS obs_return_price,
    q20.CLOSEPRICE AS exit_return_price
FROM lc_stibdailyquote q0
JOIN lc_stibdailyquote q20
  ON q20.INNERCODE = q0.INNERCODE
 AND DATE(q20.TRADINGDAY) = DATE('{exit_}')
WHERE q0.INNERCODE IN ({sql_int_list(chunk)})
  AND DATE(q0.TRADINGDAY) = DATE('{obs}')
  AND q0.CLOSEPRICE > 0
  AND q20.CLOSEPRICE > 0;
"""
        part = query(connector, sql, timeout=300)
        if not part.empty:
            frames.append(part)

    if not frames:
        raise ValueError("No future-return prices returned.")
    prices = pd.concat(frames, ignore_index=True)
    prices["INNERCODE"] = pd.to_numeric(prices["INNERCODE"], errors="coerce").astype("Int64")
    for col in ["obs_return_price", "exit_return_price"]:
        prices[col] = pd.to_numeric(prices[col], errors="coerce")
    return prices.dropna(subset=["INNERCODE", "obs_return_price", "exit_return_price"])


def fetch_source_data(connector, as_of_date: str) -> tuple[pd.DataFrame, dict[str, object]]:
    obs_date, exit_date, past_days, benchmark_return = fetch_context(connector, as_of_date)
    stocks = fetch_eligible_stocks(connector, obs_date)
    quotes = fetch_history_quotes(connector, stocks, past_days)
    return_prices = fetch_return_prices(connector, stocks, obs_date, exit_date)
    panel = stocks.merge(quotes, on="INNERCODE", how="inner")
    context = {
        "obs_date": obs_date,
        "exit_date": exit_date,
        "past_days": past_days,
        "benchmark_return": benchmark_return,
        "return_prices": return_prices,
        "eligible_stock_count": len(stocks),
    }
    return panel, context


def build_result(panel: pd.DataFrame, context: dict[str, object]) -> pd.DataFrame:
    obs_date = pd.Timestamp(context["obs_date"])
    benchmark_return = float(context["benchmark_return"])

    stock_rows: list[dict[str, object]] = []
    for _, stock_panel in panel.groupby("INNERCODE", sort=False):
        stock_panel = stock_panel.drop_duplicates("trading_day").sort_values("trading_day")
        if len(stock_panel) != HISTORY_WINDOW:
            continue
        if pd.Timestamp(stock_panel.iloc[-1]["trading_day"]) != obs_date:
            continue
        closes = stock_panel["close_price"].to_numpy(dtype=float)
        if np.isnan(closes).any() or np.any(closes <= 0):
            continue
        latest = stock_panel.iloc[-1]
        ma20 = float(np.mean(closes))
        return_10d = float(closes[-1] / closes[-(RETURN_LOOKBACK_DAYS + 1)] - 1.0)
        stock_rows.append(
            {
                "INNERCODE": int(latest["INNERCODE"]),
                "obs_date": obs_date,
                "industry_name": latest["industry_name"],
                "stock_code": latest["stock_code"],
                "stock_name": latest["stock_name"],
                "return_10d": return_10d,
                "above_ma20": int(closes[-1] > ma20),
            }
        )

    stock_df = pd.DataFrame(stock_rows)
    if stock_df.empty:
        raise ValueError("No eligible stocks with complete history and future return windows.")

    breadth = (
        stock_df.groupby("industry_name", as_index=False)["above_ma20"]
        .mean()
        .rename(columns={"above_ma20": "breadth"})
        .sort_values(["breadth", "industry_name"], ascending=[False, True], kind="mergesort")
        .head(TOP_INDUSTRIES)
        .reset_index(drop=True)
    )
    selected = stock_df.merge(breadth, on="industry_name", how="inner")
    selected = (
        selected.sort_values(
            ["breadth", "industry_name", "return_10d", "stock_code"],
            ascending=[False, True, True, True],
            kind="mergesort",
        )
        .groupby("industry_name", group_keys=False)
        .head(STOCKS_PER_INDUSTRY)
        .copy()
    )
    return_prices = context["return_prices"]
    if not isinstance(return_prices, pd.DataFrame):
        raise TypeError("return_prices must be a DataFrame")
    selected = selected.merge(return_prices, on="INNERCODE", how="inner")
    selected["excess_return_20d"] = (
        selected["exit_return_price"] / selected["obs_return_price"]
        - 1.0
        - benchmark_return
    )
    selected = selected.sort_values(
        ["obs_date", "breadth", "return_10d", "stock_code"],
        ascending=[True, False, True, True],
        kind="mergesort",
    )

    formatted = pd.DataFrame(
        {
            "观察日": selected["obs_date"].dt.strftime("%Y-%m-%d"),
            "行业名称": selected["industry_name"],
            "行业宽度": selected["breadth"].mul(100).map(lambda value: f"{value:.2f}"),
            "股票代码": selected["stock_code"],
            "股票简称": selected["stock_name"],
            "近10日收益": selected["return_10d"].mul(100).map(lambda value: f"{value:.2f}"),
            "未来20日超额收益": selected["excess_return_20d"].mul(100).map(lambda value: f"{value:.2f}"),
        },
        columns=OUTPUT_COLUMNS,
    )
    return formatted.reset_index(drop=True)


def main() -> pd.DataFrame:
    args = parse_args()
    as_of_date = validate_as_of_date(args.as_of_date)
    connector = get_db_connector()
    panel, context = fetch_source_data(connector, as_of_date)
    result = build_result(panel, context)

    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False))
    print(f"rows={len(result)}")
    return result


if __name__ == "__main__":
    main()
