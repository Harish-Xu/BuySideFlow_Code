#!/usr/bin/env python3
"""Reference implementation for stock_154."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import get_db_connector


TOP_N = 10
OUTPUT_COLUMNS = ["股票代码", "股票简称", "申万一级行业", "PB", "估值残差"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="stock_154 cross-sectional valuation reference")
    parser.add_argument("--as-of-date", default="2025-12-31")
    parser.add_argument("--output-format", choices=("json", "csv"), default="csv")
    parser.add_argument("--output-path", default="")
    return parser.parse_args()


def validate_as_of_date(value: str) -> str:
    try:
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid as_of_date: {value}") from exc


def fetch_candidates(connector, as_of_date: str) -> pd.DataFrame:
    sql = f"""
WITH
params AS (
    SELECT DATE('{as_of_date}') AS as_of_date
),
latest_trade AS (
    SELECT MAX(DATE(t.TRADINGDATE)) AS trade_date
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET IN (83, 90)
),
target_index AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '399101'
    ORDER BY INNERCODE
    LIMIT 1
),
components AS (
    SELECT DISTINCT ic.SECUINNERCODE AS INNERCODE
    FROM lc_indexcomponent ic
    JOIN target_index ti
      ON ti.INNERCODE = ic.INDEXINNERCODE
    CROSS JOIN latest_trade lt
    WHERE DATE(ic.INDATE) <= lt.trade_date
      AND (ic.OUTDATE IS NULL OR DATE(ic.OUTDATE) > lt.trade_date)
),
security_base AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        LPAD(s.SECUCODE, 6, '0') AS stock_code,
        COALESCE(s.CHINAMEABBR, s.SECUABBR, s.CHINAME, s.SECUCODE) AS stock_name
    FROM secumain s
    JOIN components c
      ON c.INNERCODE = s.INNERCODE
    WHERE s.SECUCATEGORY = 1
      AND s.COMPANYCODE IS NOT NULL
),
stock_base AS (
    SELECT sb.*
    FROM security_base sb
    CROSS JOIN latest_trade lt
    WHERE NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = sb.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 9, 12, 14)
            AND DATE(COALESCE(ra.IMPLANNOUCEDATE, ra.IMPLEMENTDATE)) <= lt.trade_date
            AND DATE(ra.IMPLEMENTDATE) <= lt.trade_date
            AND (
                ra.REMOVEDATE IS NULL
                OR DATE(ra.REMOVEDATE) > lt.trade_date
                OR (
                    ra.REMOVEINFOPUBLDATE IS NOT NULL
                    AND DATE(ra.REMOVEINFOPUBLDATE) > lt.trade_date
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
        JOIN stock_base sb
          ON sb.INNERCODE = v.INNERCODE
        CROSS JOIN latest_trade lt
        WHERE DATE(v.TRADINGDAY) = lt.trade_date
    ) ranked
    WHERE rn = 1
),
financial_index_dedup AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        OPERATINGREVENUEGROWRATE,
        NETPROFITGROWRATE,
        NETOPERATECASHFLOW
    FROM (
        SELECT
            m.COMPANYCODE,
            DATE(m.ENDDATE) AS ENDDATE,
            m.OPERATINGREVENUEGROWRATE,
            m.NETPROFITGROWRATE,
            m.NETOPERATECASHFLOW,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
                ORDER BY
                    m.INFOPUBLDATE DESC,
                    m.INSERTTIME DESC,
                    m.JSID DESC
            ) AS rn
        FROM lc_mainindexnew m
        JOIN stock_base sb
          ON sb.COMPANYCODE = m.COMPANYCODE
        JOIN params p
         ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    ) ranked
    WHERE rn = 1
),
latest_financial_index AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        OPERATINGREVENUEGROWRATE,
        NETPROFITGROWRATE,
        NETOPERATECASHFLOW
    FROM (
        SELECT
            fid.*,
            ROW_NUMBER() OVER (
                PARTITION BY fid.COMPANYCODE
                ORDER BY fid.ENDDATE DESC
            ) AS rn
        FROM financial_index_dedup fid
    ) ranked
    WHERE rn = 1
),
main_data_dedup AS (
    SELECT COMPANYCODE, ENDDATE, OPERATINGPROFIT, NETPROFIT
    FROM (
        SELECT
            m.COMPANYCODE,
            DATE(m.ENDDATE) AS ENDDATE,
            m.OPERATINGPROFIT,
            m.NETPROFIT,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
                ORDER BY
                    CASE m.MARK
                        WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                        WHEN 6 THEN 4 WHEN 2 THEN 5 ELSE 9
                    END,
                    m.INFOPUBLDATE DESC,
                    m.INSERTTIME DESC,
                    m.JSID DESC
            ) AS rn
        FROM lc_maindatanew m
        JOIN stock_base sb
          ON sb.COMPANYCODE = m.COMPANYCODE
        JOIN params p
          ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE m.MARK IN (1, 8, 7, 6, 2)
    ) ranked
    WHERE rn = 1
),
industry_asof AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND e.FIRSTINDUSTRYNAME IS NOT NULL
          AND (e.CANCELDATE IS NULL OR DATE(e.CANCELDATE) > p.as_of_date)
    ) ranked
    WHERE rn = 1
)
SELECT
    sb.stock_code,
    sb.stock_name,
    ia.FIRSTINDUSTRYNAME AS industry_name,
    lv.PB AS pb_ratio,
    lfi.OPERATINGREVENUEGROWRATE AS revenue_yoy,
    lfi.NETPROFITGROWRATE AS netprofit_yoy,
    lfi.NETOPERATECASHFLOW / NULLIF(md.OPERATINGPROFIT, 0) AS ocf_to_operating_profit
FROM stock_base sb
JOIN latest_val lv
  ON lv.INNERCODE = sb.INNERCODE
JOIN latest_financial_index lfi
  ON lfi.COMPANYCODE = sb.COMPANYCODE
JOIN main_data_dedup md
  ON md.COMPANYCODE = lfi.COMPANYCODE
 AND md.ENDDATE = lfi.ENDDATE
JOIN industry_asof ia
  ON ia.COMPANYCODE = sb.COMPANYCODE
WHERE lv.PB > 0
  AND lfi.OPERATINGREVENUEGROWRATE > 0
  AND lfi.NETPROFITGROWRATE > 0
  AND md.NETPROFIT > 0
  AND md.OPERATINGPROFIT > 0
  AND lfi.NETOPERATECASHFLOW / NULLIF(md.OPERATINGPROFIT, 0) > 0.05
ORDER BY sb.stock_code;
"""
    df = connector.execute_sql_to_dataframe(sql, max_rows=0, timeout=600)
    if df.empty:
        raise ValueError("No stock candidate returned from the database")
    df["stock_code"] = df["stock_code"].astype(str).str.zfill(6)
    for column in [
        "pb_ratio",
        "revenue_yoy",
        "netprofit_yoy",
        "ocf_to_operating_profit",
    ]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    return df.dropna().reset_index(drop=True)


def fit_valuation_model(candidates: pd.DataFrame) -> pd.DataFrame:
    feature_columns = ["revenue_yoy", "netprofit_yoy", "ocf_to_operating_profit"]
    continuous = candidates[feature_columns].astype(float)
    standard_deviation = continuous.std(ddof=0)
    usable_features = standard_deviation[standard_deviation > 0].index.tolist()
    if not usable_features:
        raise ValueError("All continuous predictors are constant")
    standardized = (
        continuous[usable_features] - continuous[usable_features].mean()
    ) / standard_deviation[usable_features]
    industry = pd.get_dummies(
        candidates["industry_name"].astype(str),
        prefix="industry",
        drop_first=True,
        dtype=float,
    )
    design = pd.concat([standardized, industry], axis=1)
    matrix = np.column_stack(
        [np.ones(len(design), dtype=float), design.to_numpy(dtype=float)]
    )
    target = np.log(candidates["pb_ratio"].to_numpy(dtype=float))
    if len(target) <= matrix.shape[1]:
        raise ValueError("Too few observations for the cross-sectional OLS model")

    coefficients, _, _, _ = np.linalg.lstsq(matrix, target, rcond=None)
    residual = target - matrix @ coefficients

    ranked = candidates.copy()
    ranked["valuation_residual"] = residual
    ranked = (
        ranked[ranked["valuation_residual"] < 0]
        .sort_values(["valuation_residual", "stock_code"], ascending=[True, True])
        .head(TOP_N)
    )
    if ranked.empty:
        raise ValueError("No negative valuation residual is available")

    result = ranked.rename(
        columns={
            "stock_code": "股票代码",
            "stock_name": "股票简称",
            "industry_name": "申万一级行业",
            "pb_ratio": "PB",
            "valuation_residual": "估值残差",
        }
    )[OUTPUT_COLUMNS].copy()
    result["PB"] = result["PB"].round(4)
    result["估值残差"] = result["估值残差"].round(4)
    return result.reset_index(drop=True)


def write_output(result: pd.DataFrame, output_format: str, output_path: str) -> None:
    if output_format == "json":
        content = json.dumps(result.to_dict(orient="records"), ensure_ascii=False, indent=2)
    else:
        buffer = io.StringIO()
        result.to_csv(buffer, index=False, float_format="%.4f")
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
        result = fit_valuation_model(fetch_candidates(connector, as_of_date))
        write_output(result, args.output_format, output_path)
        return result
    finally:
        connector.close_all()


if __name__ == "__main__":
    main()
