"""Reference implementation for stock_161: mid-cap quality screen with ROIC check."""

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


MIN_LISTING_DAYS = 375
MIN_MARKET_CAP = 10_000_000_000
MAX_MARKET_CAP = 50_000_000_000
MAX_PE = 50
MIN_EPS = 0.12
MIN_ROA = 5
MAX_LIABILITY_TO_EQUITY = 0.5
MIN_REVENUE_GROWTH = 10
PRE_ROIC_LIMIT = 10
MIN_ROIC_TTM = 8
RISK_ALERT_TYPES = "1, 5, 12, 14"
OUTPUT_COLUMNS = [
    "trade_date",
    "stock_code",
    "stock_name",
    "report_end_date",
    "market_cap",
    "retained_profit",
    "roic_ttm",
]


def canonical_as_of_date() -> str:
    from sweagent.text2sql import db_connector
    root = Path(db_connector.__file__).resolve().parents[2]
    with (root / "data/dataset/question/dataset.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            task = json.loads(line)
            if task["instance_id"] == "stock_161":
                return validate_as_of_date(task["inputs"]["as_of_date"])
    raise ValueError("stock_161 missing from canonical dataset")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="stock_161 mid-cap quality screen reference implementation."
    )
    parser.add_argument("--as-of-date", default=None)
    parser.add_argument("--output-format", choices=("json", "csv"), default="csv")
    parser.add_argument("--output-path", default="")
    return parser.parse_args()


def validate_as_of_date(as_of_date: str) -> str:
    try:
        return pd.Timestamp(as_of_date).strftime("%Y-%m-%d")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid as_of_date: {as_of_date}") from exc


def build_sql(as_of_date: str, innercodes: list[int] | None = None) -> str:
    as_of_date = validate_as_of_date(as_of_date)
    code_filter = ""
    if innercodes is not None:
        if not innercodes:
            raise ValueError("Empty candidate batch")
        code_filter = "AND s.INNERCODE IN (" + ",".join(str(int(x)) for x in innercodes) + ")"
    return f"""
WITH params AS (
    SELECT DATE('{as_of_date}') AS as_of_date
),
trade_date AS (
    SELECT MAX(DATE(t.TRADINGDATE)) AS trade_date
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET IN (83, 90)
),
stock_base AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        s.SECUCODE AS stock_code,
        COALESCE(s.CHINAMEABBR, s.SECUABBR, s.CHINAME, s.SECUCODE) AS stock_name
    FROM secumain s
    JOIN trade_date td
    CROSS JOIN params p
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR IN (1, 2, 6)
      {code_filter}
      AND s.COMPANYCODE IS NOT NULL
      AND s.LISTEDDATE <= DATE_SUB(p.as_of_date, INTERVAL {MIN_LISTING_DAYS} DAY)
      AND NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = s.INNERCODE
            AND ra.RISKALERTTYPE IN ({RISK_ALERT_TYPES})
            AND ra.IMPLEMENTDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
            AND COALESCE(ra.IMPLANNOUCEDATE, ra.IMPLEMENTDATE)
                < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
            AND (
                ra.REMOVEDATE IS NULL
                OR ra.REMOVEDATE >= DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
                OR (
                    ra.REMOVEINFOPUBLDATE IS NOT NULL
                    AND ra.REMOVEINFOPUBLDATE >= DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
                )
            )
      )
),
market_snapshot AS (
    SELECT
        sb.INNERCODE,
        v.TOTALMV AS market_cap,
        v.PE AS pe_ratio
    FROM stock_base sb
    JOIN trade_date td
    JOIN lc_dindicesforvaluation v FORCE INDEX (IX_LC_DIFV)
      ON v.INNERCODE = sb.INNERCODE
     AND v.TRADINGDAY >= td.trade_date
     AND v.TRADINGDAY < DATE_ADD(td.trade_date, INTERVAL 1 DAY)
    JOIN qt_dailyquote q
      ON q.INNERCODE = sb.INNERCODE
     AND q.TRADINGDAY >= td.trade_date
     AND q.TRADINGDAY < DATE_ADD(td.trade_date, INTERVAL 1 DAY)
    WHERE v.TOTALMV BETWEEN {MIN_MARKET_CAP} AND {MAX_MARKET_CAP}
      AND v.PE > 0
      AND v.PE <= {MAX_PE}
      AND NOT EXISTS (
          SELECT 1 FROM lc_suspendresumption sr
          WHERE sr.INNERCODE = sb.INNERCODE
            AND sr.SUSPENDDATE < DATE_ADD(td.trade_date, INTERVAL 1 DAY)
            AND COALESCE(sr.INFOPUBLDATE, sr.INSERTTIME) < DATE_ADD(td.trade_date, INTERVAL 1 DAY)
            AND (sr.RESUMPTIONDATE IS NULL OR sr.RESUMPTIONDATE >= DATE_ADD(td.trade_date, INTERVAL 1 DAY))
      )
),
main_data AS (
    SELECT *
    FROM (
        SELECT
            sb.COMPANYCODE,
            DATE(m.ENDDATE) AS report_end_date,
            m.EPS,
            m.RETAINEDPROFIT,
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
        JOIN market_snapshot ms ON ms.INNERCODE = sb.INNERCODE
        JOIN lc_maindatanew m
          ON m.COMPANYCODE = sb.COMPANYCODE
        JOIN params p
          ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE m.ENDDATE <= p.as_of_date AND m.MARK IN (1, 8, 7, 6, 2)
    ) x
    WHERE rn = 1
),
main_index AS (
    SELECT *
    FROM (
        SELECT
            sb.COMPANYCODE,
            DATE(mi.ENDDATE) AS report_end_date,
            mi.ROA,
            mi.ROICTTM,
            mi.OPERATINGREVENUEGROWRATE,
            mi.DEBTEQUITYRATIO,
            ROW_NUMBER() OVER (
                PARTITION BY sb.COMPANYCODE, DATE(mi.ENDDATE)
                ORDER BY mi.INFOPUBLDATE DESC, mi.INSERTTIME DESC, mi.ID DESC
            ) AS rn
        FROM stock_base sb
        JOIN market_snapshot ms ON ms.INNERCODE = sb.INNERCODE
        JOIN lc_mainindexnew mi
          ON mi.COMPANYCODE = sb.COMPANYCODE
        JOIN params p
          ON mi.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE mi.ENDDATE <= p.as_of_date
    ) x
    WHERE rn = 1
),
financial_aligned AS (
    SELECT *
    FROM (
        SELECT
            md.COMPANYCODE,
            md.report_end_date,
            md.EPS,
            mi.ROA,
            mi.ROICTTM,
            mi.OPERATINGREVENUEGROWRATE,
            mi.DEBTEQUITYRATIO / 100 AS liability_to_equity_ratio,
            md.RETAINEDPROFIT,
            ROW_NUMBER() OVER (
                PARTITION BY md.COMPANYCODE
                ORDER BY md.report_end_date DESC
            ) AS rn
        FROM main_data md
        JOIN main_index mi
          ON mi.COMPANYCODE = md.COMPANYCODE
         AND mi.report_end_date = md.report_end_date
    ) x
    WHERE rn = 1
),
pre_roic_ranked AS (
    SELECT
        p.as_of_date,
        td.trade_date,
        sb.stock_code,
        sb.stock_name,
        ms.market_cap,
        ms.pe_ratio,
        f.report_end_date,
        f.EPS AS eps,
        f.ROA AS roa,
        f.ROICTTM AS roic_ttm,
        f.OPERATINGREVENUEGROWRATE AS operating_revenue_growth_rate,
        f.liability_to_equity_ratio,
        f.RETAINEDPROFIT AS retained_profit,
        ROW_NUMBER() OVER (
            ORDER BY f.RETAINEDPROFIT DESC, sb.stock_code ASC
        ) AS retained_rank
    FROM stock_base sb
    JOIN params p
    JOIN trade_date td
    JOIN market_snapshot ms
      ON ms.INNERCODE = sb.INNERCODE
    JOIN financial_aligned f
      ON f.COMPANYCODE = sb.COMPANYCODE
    WHERE f.EPS > {MIN_EPS}
      AND f.ROA > {MIN_ROA}
      AND f.liability_to_equity_ratio < {MAX_LIABILITY_TO_EQUITY}
      AND f.OPERATINGREVENUEGROWRATE > {MIN_REVENUE_GROWTH}
      AND f.RETAINEDPROFIT > 0
)
SELECT
    DATE_FORMAT(trade_date, '%Y-%m-%d') AS trade_date,
    stock_code,
    stock_name,
    DATE_FORMAT(report_end_date, '%Y-%m-%d') AS report_end_date,
    market_cap,
    retained_profit,
    roic_ttm
FROM pre_roic_ranked
WHERE retained_rank <= {PRE_ROIC_LIMIT}
  AND roic_ttm > {MIN_ROIC_TTM}
ORDER BY retained_profit DESC, stock_code ASC;
"""


def fetch_result(connector, as_of_date: str) -> pd.DataFrame:
    as_of_date = validate_as_of_date(as_of_date)
    master = connector.execute_sql_to_dataframe(f"""
SELECT INNERCODE FROM secumain
WHERE SECUCATEGORY=1 AND SECUMARKET IN (83,90) AND LISTEDSECTOR IN (1,2,6)
  AND LISTEDDATE <= DATE_SUB('{as_of_date}', INTERVAL {MIN_LISTING_DAYS} DAY)
ORDER BY INNERCODE
""", max_rows=0, timeout=60)
    ids = master['INNERCODE'].astype(int).tolist()
    parts = []
    for offset in range(0, len(ids), 100):
        sql = build_sql(as_of_date, ids[offset:offset+100])
        # Batches constrain database reads only; top ten and ROIC are global.
        sql = sql.replace(f"WHERE retained_rank <= {PRE_ROIC_LIMIT}\n  AND roic_ttm > {MIN_ROIC_TTM}\n", "")
        part = connector.execute_sql_to_dataframe(sql, max_rows=0, timeout=120)
        if not part.empty:
            parts.append(part)
    if not parts:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    result = pd.concat(parts, ignore_index=True)
    result["stock_code"] = result["stock_code"].astype(str).str.zfill(6)
    if result['stock_code'].duplicated().any():
        raise ValueError("Duplicate stock in screening output")
    result = result.sort_values(['retained_profit', 'stock_code'], ascending=[False, True]).head(PRE_ROIC_LIMIT)
    result = result[result['roic_ttm'] > MIN_ROIC_TTM]
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
    as_of_date = validate_as_of_date(args.as_of_date or canonical_as_of_date())
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
