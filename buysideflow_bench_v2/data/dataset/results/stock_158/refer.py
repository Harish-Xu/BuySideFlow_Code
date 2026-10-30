"""Reference implementation for stock_158: Guojiutiao dividend-risk screening."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import get_db_connector


INDEX_CODE = "399101"
MIN_CASH_DIVIDEND_3Y = 50_000_000
MIN_DIVIDEND_TO_PROFIT_RATIO = 0.30
OUTPUT_COLUMNS = [
    "as_of_date",
    "stock_code",
    "stock_name",
    "cash_dividend_3y",
    "avg_net_profit_3y",
    "dividend_to_profit_ratio_3y",
    "market_cap",
    "risk_flag",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="stock_158 Guojiutiao dividend-risk screening reference."
    )
    parser.add_argument("--as-of-date", default="2025-05-31")
    parser.add_argument("--output-format", choices=("json", "csv"), default="csv")
    parser.add_argument("--output-path", default="")
    return parser.parse_args()


def validate_as_of_date(as_of_date: str) -> str:
    try:
        return pd.Timestamp(as_of_date).strftime("%Y-%m-%d")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid as_of_date: {as_of_date}") from exc


def latest_complete_fiscal_year(as_of_date: str) -> int:
    ts = pd.Timestamp(as_of_date)
    return ts.year - 1 if (ts.month, ts.day) >= (4, 30) else ts.year - 2


def build_sql(as_of_date: str) -> str:
    end_year = latest_complete_fiscal_year(as_of_date)
    start_year = end_year - 2
    return f"""
WITH params AS (
    SELECT
        DATE('{as_of_date}') AS as_of_date,
        {start_year} AS start_year,
        {end_year} AS end_year
),
trade_date AS (
    SELECT MAX(DATE(t.TRADINGDATE)) AS trade_date
    FROM qt_tradingdaynew t
    JOIN params p
      ON DATE(t.TRADINGDATE) <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET IN (83, 90)
),
target_index AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '{INDEX_CODE}'
    ORDER BY INNERCODE
    LIMIT 1
),
components AS (
    SELECT DISTINCT ic.SECUINNERCODE AS INNERCODE
    FROM lc_indexcomponent ic
    JOIN target_index ti
      ON ti.INNERCODE = ic.INDEXINNERCODE
    JOIN params p
      ON ic.INDATE <= p.as_of_date
    WHERE ic.OUTDATE IS NULL OR ic.OUTDATE > p.as_of_date
),
stock_base AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        s.SECUCODE AS stock_code,
        COALESCE(s.CHINAMEABBR, s.SECUABBR, s.CHINAME, s.SECUCODE) AS stock_name
    FROM secumain s
    JOIN components c
      ON c.INNERCODE = s.INNERCODE
    JOIN params p
      ON DATE(s.LISTEDDATE) <= DATE_SUB(p.as_of_date, INTERVAL 1 YEAR)
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND COALESCE(s.LISTEDSECTOR, 0) NOT IN (7, 8)
      AND NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = s.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND DATE(ra.IMPLEMENTDATE) <= p.as_of_date
            AND DATE(COALESCE(ra.IMPLANNOUCEDATE, ra.IMPLEMENTDATE)) <= p.as_of_date
            AND (
                ra.REMOVEDATE IS NULL
                OR DATE(ra.REMOVEDATE) > p.as_of_date
                OR (
                    ra.REMOVEINFOPUBLDATE IS NOT NULL
                    AND DATE(ra.REMOVEINFOPUBLDATE) > p.as_of_date
                )
            )
      )
),
net_profit_raw AS (
    SELECT
        sb.COMPANYCODE,
        YEAR(m.ENDDATE) AS fiscal_year,
        m.NPFROMPARENTCOMPANYOWNERS AS net_profit,
        ROW_NUMBER() OVER (
            PARTITION BY sb.COMPANYCODE, DATE(m.ENDDATE)
            ORDER BY
                CASE m.MARK
                    WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                    WHEN 6 THEN 4 WHEN 2 THEN 5 ELSE 9
                END,
                m.INFOPUBLDATE DESC,
                m.INSERTTIME DESC,
                m.ID DESC
        ) AS rn
    FROM stock_base sb
    JOIN lc_maindatanew m
      ON m.COMPANYCODE = sb.COMPANYCODE
    JOIN params p
      ON m.INFOPUBLDATE <= p.as_of_date
    WHERE YEAR(m.ENDDATE) BETWEEN p.start_year AND p.end_year
      AND MONTH(m.ENDDATE) = 12
      AND DAY(m.ENDDATE) = 31
      AND m.NPFROMPARENTCOMPANYOWNERS IS NOT NULL
      AND m.MARK IN (1, 8, 7, 6, 2)
),
net_profit_3y AS (
    SELECT
        COMPANYCODE,
        AVG(net_profit) AS avg_net_profit_3y,
        COUNT(*) AS profit_year_count
    FROM net_profit_raw
    WHERE rn = 1
    GROUP BY COMPANYCODE
),
dividend_event AS (
    SELECT
        sb.INNERCODE,
        d.TOTALCASHDIVICOMRMB,
        ROW_NUMBER() OVER (
            PARTITION BY sb.INNERCODE, DATE(d.ENDDATE), COALESCE(d.PROPOSALSN, 0)
            ORDER BY DATE(d.SMDECIPUBLDATE) DESC, d.JSID DESC
        ) AS rn
    FROM stock_base sb
    JOIN lc_dividend d
      ON d.INNERCODE = sb.INNERCODE
    JOIN params p
      ON d.SMDECIPUBLDATE <= p.as_of_date
    WHERE YEAR(d.ENDDATE) BETWEEN p.start_year AND p.end_year
      AND d.IFDIVIDEND = 1
      AND d.TOTALCASHDIVICOMRMB IS NOT NULL
),
dividend_3y AS (
    SELECT
        INNERCODE,
        SUM(TOTALCASHDIVICOMRMB) AS cash_dividend_3y
    FROM dividend_event
    WHERE rn = 1
    GROUP BY INNERCODE
),
valuation AS (
    SELECT
        v.INNERCODE,
        v.TOTALMV AS market_cap
    FROM lc_dindicesforvaluation v
    JOIN trade_date td
      ON DATE(v.TRADINGDAY) = td.trade_date
    WHERE v.TOTALMV IS NOT NULL
)
SELECT
    DATE_FORMAT(p.as_of_date, '%Y-%m-%d') AS as_of_date,
    sb.stock_code,
    sb.stock_name,
    COALESCE(d.cash_dividend_3y, 0) AS cash_dividend_3y,
    np.avg_net_profit_3y,
    CASE
        WHEN np.avg_net_profit_3y <> 0
        THEN COALESCE(d.cash_dividend_3y, 0) / np.avg_net_profit_3y
        ELSE NULL
    END AS dividend_to_profit_ratio_3y,
    v.market_cap,
    1 AS risk_flag
FROM stock_base sb
JOIN net_profit_3y np
  ON np.COMPANYCODE = sb.COMPANYCODE
 AND np.profit_year_count = 3
LEFT JOIN dividend_3y d
  ON d.INNERCODE = sb.INNERCODE
JOIN valuation v
  ON v.INNERCODE = sb.INNERCODE
JOIN params p
WHERE COALESCE(d.cash_dividend_3y, 0) < {MIN_CASH_DIVIDEND_3Y}
  AND np.avg_net_profit_3y > 0
  AND COALESCE(d.cash_dividend_3y, 0)
      < np.avg_net_profit_3y * {MIN_DIVIDEND_TO_PROFIT_RATIO}
ORDER BY v.market_cap ASC, sb.stock_code ASC
LIMIT 10;
"""


def fetch_result(connector, as_of_date: str) -> pd.DataFrame:
    result = connector.execute_sql_to_dataframe(build_sql(as_of_date), max_rows=0)
    if result.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    result["stock_code"] = result["stock_code"].astype(str).str.zfill(6)
    return result[OUTPUT_COLUMNS]


def write_output(result: pd.DataFrame, output_format: str, output_path: str) -> None:
    if output_format == "json":
        content = json.dumps(result.to_dict(orient="records"), ensure_ascii=False, indent=2)
    else:
        buf = io.StringIO()
        result.to_csv(buf, index=False)
        content = buf.getvalue()

    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"Saved output to {path}")
        return
    print(content)


def main() -> None:
    args = parse_args()
    as_of_date = validate_as_of_date(args.as_of_date)
    if not args.output_path:
        args.output_path = str(Path(__file__).with_name("result.csv"))
    if args.output_format == "json" and args.output_path.endswith(".csv"):
        args.output_format = "csv"

    connector = get_db_connector()
    try:
        result = fetch_result(connector, as_of_date)
        write_output(result, args.output_format, args.output_path)
    finally:
        connector.close_all()


if __name__ == "__main__":
    main()
