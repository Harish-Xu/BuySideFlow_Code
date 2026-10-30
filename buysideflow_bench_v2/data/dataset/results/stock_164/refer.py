#!/usr/bin/env python3
"""Reference implementation for stock_164."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import sys
from typing import Iterable

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import get_db_connector


ETF_POOL = ["510180", "159915", "513100", "518880"]
ETF_WEIGHT = 0.60
STOCK_WEIGHT = 0.40
SHORT_LAG = 25
LONG_LAG = 200
STOCK_COUNT = 5
OUTPUT_COLUMNS = ["观察日", "交易日", "子策略", "证券代码", "证券名称", "目标权重"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="stock_164 target allocation reference")
    parser.add_argument("--as-of-date", default="2025-12-31")
    parser.add_argument("--etf-pool", nargs="*", default=ETF_POOL)
    parser.add_argument("--output-format", choices=("json", "csv"), default="csv")
    parser.add_argument("--output-path", default="")
    return parser.parse_args()


def validate_as_of_date(value: str) -> str:
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid as_of_date: {value}") from exc


def next_day(value: str | pd.Timestamp) -> str:
    return (pd.Timestamp(value) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def sql_code_list(codes: Iterable[str]) -> str:
    normalized: list[str] = []
    for code in codes:
        value = str(code).strip().zfill(6)
        if not value.isdigit():
            raise ValueError(f"Security code must be numeric: {code}")
        normalized.append(f"'{value}'")
    if not normalized:
        raise ValueError("ETF pool cannot be empty")
    return ",".join(normalized)


def sql_int_list(values: Iterable[int]) -> str:
    normalized = sorted(set(int(value) for value in values))
    if not normalized:
        raise ValueError("No integer value is available for the SQL IN clause")
    return ",".join(str(value) for value in normalized)


def query(connector, sql: str, timeout: int = 180) -> pd.DataFrame:
    result = connector.execute_sql_to_dataframe(sql, max_rows=0, timeout=timeout)
    if result is None:
        raise RuntimeError("SQL returned None")
    return result


def fetch_trade_date(connector, as_of_date: str) -> str:
    result = query(
        connector,
        f"""
SELECT MAX(DATE(TRADINGDATE)) AS trade_date
FROM qt_tradingdaynew
WHERE IFTRADINGDAY = 1
  AND SECUMARKET IN (83, 90)
  AND TRADINGDATE < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY);
""",
        timeout=60,
    )
    if result.empty or pd.isna(result.loc[0, "trade_date"]):
        raise ValueError(f"No A-share trading day found on or before {as_of_date}")
    return pd.Timestamp(result.loc[0, "trade_date"]).strftime("%Y-%m-%d")


def fetch_etf_prices(
    connector,
    trade_date: str,
    etf_pool: Iterable[str],
) -> pd.DataFrame:
    result = query(
        connector,
        f"""
WITH trade_days AS (
    SELECT trading_day, ROW_NUMBER() OVER (ORDER BY trading_day DESC) AS rn_desc
    FROM (
        SELECT DISTINCT DATE(TRADINGDATE) AS trading_day
        FROM qt_tradingdaynew
        WHERE IFTRADINGDAY = 1
          AND SECUMARKET IN (83, 90)
          AND TRADINGDATE < DATE_ADD(DATE('{trade_date}'), INTERVAL 1 DAY)
    ) d
), anchors AS (
    SELECT
        MAX(CASE WHEN rn_desc = 1 THEN trading_day END) AS current_day,
        MAX(CASE WHEN rn_desc = {SHORT_LAG + 1} THEN trading_day END) AS day_25,
        MAX(CASE WHEN rn_desc = {LONG_LAG + 1} THEN trading_day END) AS day_200
    FROM trade_days
)
SELECT
    LPAD(s.SECUCODE, 6, '0') AS etf_code,
    COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS etf_name,
    MAX(CASE WHEN DATE(q.TRADINGDAY) = a.current_day THEN q.CLOSEPRICE END) AS current_close,
    MAX(CASE WHEN DATE(q.TRADINGDAY) = a.day_25 THEN q.CLOSEPRICE END) AS close_25d_ago,
    MAX(CASE WHEN DATE(q.TRADINGDAY) = a.day_200 THEN q.CLOSEPRICE END) AS close_200d_ago
FROM secumain s
CROSS JOIN anchors a
LEFT JOIN qt_fundsperformancehis q
  ON q.INNERCODE = s.INNERCODE
 AND DATE(q.TRADINGDAY) IN (a.current_day, a.day_25, a.day_200)
WHERE s.SECUCATEGORY = 8
  AND s.SECUCODE IN ({sql_code_list(etf_pool)})
GROUP BY s.INNERCODE, s.SECUCODE, s.SECUABBR, s.CHINAMEABBR, s.CHINAME
ORDER BY s.SECUCODE;
""",
        timeout=180,
    )
    if result.empty:
        return pd.DataFrame(
            columns=["etf_code", "etf_name", "current_close", "close_25d_ago", "close_200d_ago"]
        )
    result["etf_code"] = result["etf_code"].astype(str).str.zfill(6)
    for column in ["current_close", "close_25d_ago", "close_200d_ago"]:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.drop_duplicates("etf_code", keep="first").reset_index(drop=True)


def cash_row(as_of_date: str, trade_date: str, sleeve: str, weight: float) -> dict[str, object]:
    return {
        "观察日": as_of_date,
        "交易日": trade_date,
        "子策略": sleeve,
        "证券代码": "CASH",
        "证券名称": "现金",
        "目标权重": weight,
    }


def target_row(
    as_of_date: str,
    trade_date: str,
    sleeve: str,
    code: str,
    name: str,
    weight: float,
) -> dict[str, object]:
    return {
        "观察日": as_of_date,
        "交易日": trade_date,
        "子策略": sleeve,
        "证券代码": str(code),
        "证券名称": str(name),
        "目标权重": weight,
    }


def build_etf_sleeve(
    connector,
    as_of_date: str,
    trade_date: str,
    etf_pool: Iterable[str],
) -> list[dict[str, object]]:
    sleeve = "ETF轮动"
    prices = fetch_etf_prices(connector, trade_date, etf_pool)
    candidates: list[dict[str, object]] = []
    for code in [str(value).zfill(6) for value in etf_pool]:
        row = prices[prices["etf_code"] == code]
        if row.empty:
            continue
        row = row.iloc[0]
        if pd.isna(row[["current_close", "close_25d_ago", "close_200d_ago"]]).any():
            continue
        latest_price = float(row["current_close"])
        return_25d = latest_price / float(row["close_25d_ago"]) - 1
        return_200d = latest_price / float(row["close_200d_ago"]) - 1
        if return_25d > 0 and return_200d > 0:
            candidates.append(
                {
                    "code": code,
                    "name": row["etf_name"],
                    "return_25d": return_25d,
                }
            )
    if not candidates:
        return [cash_row(as_of_date, trade_date, sleeve, ETF_WEIGHT)]
    selected = sorted(candidates, key=lambda row: (-row["return_25d"], row["code"]))[0]
    return [
        target_row(
            as_of_date,
            trade_date,
            sleeve,
            selected["code"],
            selected["name"],
            ETF_WEIGHT,
        )
    ]


def fetch_stock_market(connector, trade_date: str) -> pd.DataFrame:
    result = query(
        connector,
        f"""
WITH
security_base AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        LPAD(s.SECUCODE, 6, '0') AS stock_code,
        COALESCE(s.CHINAMEABBR, s.SECUABBR, s.CHINAME, s.SECUCODE) AS stock_name
    FROM secumain s
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR IN (1, 2, 6)
      AND s.COMPANYCODE IS NOT NULL
      AND DATE(s.LISTEDDATE) <= DATE('{trade_date}')
),
latest_status AS (
    SELECT INNERCODE, CHANGETYPE
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY
                    DATE(ls.CHANGEDATE) DESC,
                    ls.INSERTTIME DESC,
                    ls.JSID DESC
            ) AS rn
        FROM lc_liststatus ls
        JOIN security_base sb
          ON sb.INNERCODE = ls.INNERCODE
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
          AND DATE(ls.CHANGEDATE) <= DATE('{trade_date}')
          AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME
                < DATE_ADD(DATE('{trade_date}'), INTERVAL 1 DAY))
    ) ranked
    WHERE rn = 1
),
active_stock AS (
    SELECT sb.*
    FROM security_base sb
    LEFT JOIN latest_status ls
      ON ls.INNERCODE = sb.INNERCODE
    WHERE ls.CHANGETYPE IN (1, 3)
      AND NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = sb.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND DATE(COALESCE(ra.IMPLANNOUCEDATE, ra.IMPLEMENTDATE)) <= DATE('{trade_date}')
            AND DATE(ra.IMPLEMENTDATE) <= DATE('{trade_date}')
            AND (
                ra.REMOVEDATE IS NULL
                OR DATE(ra.REMOVEDATE) > DATE('{trade_date}')
                OR (
                    ra.REMOVEINFOPUBLDATE IS NOT NULL
                    AND DATE(ra.REMOVEINFOPUBLDATE) > DATE('{trade_date}')
                )
            )
      )
),
latest_val AS (
    SELECT INNERCODE, PB
    FROM (
        SELECT
            v.INNERCODE,
            v.PB,
            ROW_NUMBER() OVER (
                PARTITION BY v.INNERCODE
                ORDER BY v.JSID DESC
            ) AS rn
        FROM lc_dindicesforvaluation v
        JOIN active_stock s
          ON s.INNERCODE = v.INNERCODE
        WHERE DATE(v.TRADINGDAY) = DATE('{trade_date}')
          AND v.PB > 0
          AND v.PB < 1
    ) ranked
    WHERE rn = 1
)
SELECT
    s.INNERCODE,
    s.COMPANYCODE,
    s.stock_code,
    s.stock_name,
    v.PB AS pb_ratio
FROM active_stock s
JOIN latest_val v
  ON v.INNERCODE = s.INNERCODE
JOIN qt_dailyquote q
  ON q.INNERCODE = s.INNERCODE
 AND DATE(q.TRADINGDAY) = DATE('{trade_date}')
WHERE q.CLOSEPRICE > 0
  AND COALESCE(q.TURNOVERVOLUME, 0) > 0
ORDER BY s.stock_code;
""",
        timeout=300,
    )
    if result.empty:
        return pd.DataFrame(
            columns=["INNERCODE", "COMPANYCODE", "stock_code", "stock_name", "pb_ratio"]
        )
    result["INNERCODE"] = pd.to_numeric(result["INNERCODE"], errors="coerce").astype(int)
    result["COMPANYCODE"] = pd.to_numeric(result["COMPANYCODE"], errors="coerce").astype(int)
    result["stock_code"] = result["stock_code"].astype(str).str.zfill(6)
    result["pb_ratio"] = pd.to_numeric(result["pb_ratio"], errors="coerce")
    return result.drop_duplicates("INNERCODE", keep="last").reset_index(drop=True)


def fetch_latest_financial(
    connector,
    trade_date: str,
    company_codes: Iterable[int],
) -> pd.DataFrame:
    code_clause = sql_int_list(company_codes)
    result = query(
        connector,
        f"""
WITH dedup AS (
    SELECT
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS report_end_date,
        m.NETPROFITCUT AS net_profit_cut,
        m.ROA,
        m.OPERPROFITGROWRATE AS operating_profit_yoy,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
            ORDER BY
                m.INFOPUBLDATE DESC,
                m.INSERTTIME DESC,
                m.JSID DESC
        ) AS period_rank
    FROM lc_mainindexnew m
    WHERE m.COMPANYCODE IN ({code_clause})
      AND m.INFOPUBLDATE < DATE('{next_day(trade_date)}')
),
latest AS (
    SELECT
        d.*,
        ROW_NUMBER() OVER (
            PARTITION BY d.COMPANYCODE
            ORDER BY d.report_end_date DESC
        ) AS company_rank
    FROM dedup d
    WHERE d.period_rank = 1
)
SELECT COMPANYCODE, report_end_date, net_profit_cut, ROA, operating_profit_yoy
FROM latest
WHERE company_rank = 1
  AND net_profit_cut > 0
  AND ROA IS NOT NULL
  AND operating_profit_yoy > 0;
""",
        timeout=240,
    )
    if result.empty:
        return pd.DataFrame(
            columns=["COMPANYCODE", "report_end_date", "net_profit_cut", "ROA", "operating_profit_yoy"]
        )
    result["COMPANYCODE"] = pd.to_numeric(result["COMPANYCODE"], errors="coerce").astype(int)
    for column in ["net_profit_cut", "ROA", "operating_profit_yoy"]:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.dropna(subset=["ROA"]).reset_index(drop=True)


def build_stock_sleeve(
    connector,
    as_of_date: str,
    trade_date: str,
) -> list[dict[str, object]]:
    sleeve = "低PB股票"
    market = fetch_stock_market(connector, trade_date)
    if market.empty:
        return [cash_row(as_of_date, trade_date, sleeve, STOCK_WEIGHT)]
    financial = fetch_latest_financial(connector, trade_date, market["COMPANYCODE"])
    selected = (
        market.merge(financial, on="COMPANYCODE", how="inner")
        .sort_values(["ROA", "stock_code"], ascending=[False, True])
        .head(STOCK_COUNT)
    )
    if selected.empty:
        return [cash_row(as_of_date, trade_date, sleeve, STOCK_WEIGHT)]

    weight = STOCK_WEIGHT / len(selected)
    return [
        target_row(
            as_of_date,
            trade_date,
            sleeve,
            row.stock_code,
            row.stock_name,
            weight,
        )
        for row in selected.itertuples(index=False)
    ]


def compute_result(
    connector,
    as_of_date: str,
    etf_pool: Iterable[str],
) -> pd.DataFrame:
    trade_date = fetch_trade_date(connector, as_of_date)
    rows = build_etf_sleeve(connector, as_of_date, trade_date, etf_pool)
    rows.extend(build_stock_sleeve(connector, as_of_date, trade_date))
    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def write_output(result: pd.DataFrame, output_format: str, output_path: str) -> None:
    if output_format == "json":
        content = json.dumps(result.to_dict(orient="records"), ensure_ascii=False, indent=2)
    else:
        buffer = io.StringIO()
        result.to_csv(buffer, index=False, float_format="%.6f")
        content = buffer.getvalue()
    path = Path(output_path) if output_path else Path(__file__).with_name("result.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(result.to_string(index=False))


def main() -> pd.DataFrame:
    args = parse_args()
    as_of_date = validate_as_of_date(args.as_of_date)
    output_path = args.output_path or str(Path(__file__).with_name("result.csv"))
    connector = get_db_connector()
    try:
        result = compute_result(connector, as_of_date, args.etf_pool)
        write_output(result, args.output_format, output_path)
        return result
    finally:
        connector.close_all()


if __name__ == "__main__":
    main()
