#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_MODE = Path(globals().get("__file__", "")).name == "refer.py"
if SCRIPT_MODE:
    REPO_ROOT = Path(__file__).resolve().parents[4]
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

from benchmark_paths import load_benchmark_env
from sweagent.text2sql.db_connector import query_to_dataframe
from sweagent.text2sql.provenance import require_db_snapshot_id

AS_OF_DATE = "2025-04-01"
PERIOD_START = "2024-12-31"
PERIOD_END = "2025-03-31"
OUTPUT_COLUMNS = ["基金股票组合收益", "基准组合收益", "超额收益", "配置贡献", "选股贡献", "交互贡献"]


def run_sql(sql: str) -> pd.DataFrame:
    data = query_to_dataframe(sql, max_rows=0, timeout=300)
    if data is None:
        raise ValueError("SQL execution returned None")
    return data


def resolve_meta() -> tuple[int, int]:
    data = run_sql("""
SELECT INNERCODE, SECUCODE, SECUCATEGORY
FROM secumain
WHERE (SECUCODE = '003884' AND SECUCATEGORY = 8)
   OR (SECUCODE = '000300' AND SECUCATEGORY = 4)
""")
    fund = data.loc[data.SECUCATEGORY == 8, "INNERCODE"]
    bench = data.loc[data.SECUCATEGORY == 4, "INNERCODE"]
    if len(fund) != 1 or len(bench) != 1:
        raise ValueError("Fund/index code must resolve uniquely")
    return int(fund.iloc[0]), int(bench.iloc[0])


def load_fund_holdings(innercode: int) -> pd.DataFrame:
    data = run_sql(f"""
WITH ranked AS (
    SELECT d.STOCKINNERCODE AS stock_innercode, d.MARKETVALUE AS weight_raw,
           ROW_NUMBER() OVER (
               PARTITION BY d.INNERCODE, d.REPORTDATE, d.INVESTTYPE, d.STOCKINNERCODE
               ORDER BY d.INFOPUBLDATE DESC, d.ID DESC
           ) AS rn
    FROM mf_stockportfoliodetail d
    WHERE d.INNERCODE = {int(innercode)}
      AND d.REPORTDATE = '{PERIOD_START}'
      AND d.INFOPUBLDATE < '{AS_OF_DATE}'
)
SELECT stock_innercode, SUM(weight_raw) AS weight_raw
FROM ranked
WHERE rn = 1 AND stock_innercode IS NOT NULL AND weight_raw > 0
GROUP BY stock_innercode
ORDER BY stock_innercode
""")
    if data.empty:
        raise ValueError("No annual full-stock holdings disclosed before observation date")
    return data


def load_bench_components(innercode: int) -> pd.DataFrame:
    # No publication field exists here; UPDATETIME cannot prove first availability.
    data = run_sql(f"""
SELECT INNERCODE AS stock_innercode, WEIGHT AS weight_raw
FROM lc_indexcomponentsweight
WHERE INDEXCODE = {int(innercode)} AND ENDDATE = '{PERIOD_START}'
ORDER BY INNERCODE, ID
""")
    if data.empty or data.stock_innercode.duplicated().any():
        raise ValueError("Exact-date index weights missing or ambiguous")
    return data.loc[data.weight_raw > 0].copy()


def load_stock_returns(codes: list[int]) -> dict[int, float]:
    code_list = ",".join(str(int(x)) for x in sorted(set(codes)))
    data = run_sql(f"""
WITH prices AS (
    SELECT INNERCODE, TRADINGDAY, CLOSEPRICE, 1 AS source_priority, ID
    FROM qt_dailyquote
    WHERE INNERCODE IN ({code_list})
      AND TRADINGDAY IN ('{PERIOD_START}', '{PERIOD_END}')
    UNION ALL
    SELECT INNERCODE, TRADINGDAY, CLOSEPRICE, 2 AS source_priority, ID
    FROM lc_stibdailyquote
    WHERE INNERCODE IN ({code_list})
      AND TRADINGDAY IN ('{PERIOD_START}', '{PERIOD_END}')
), ranked AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY INNERCODE, TRADINGDAY ORDER BY source_priority, ID DESC
    ) AS rn FROM prices
)
SELECT INNERCODE AS stock_innercode,
       MAX(CASE WHEN TRADINGDAY = '{PERIOD_START}' THEN CLOSEPRICE END) AS start_price,
       MAX(CASE WHEN TRADINGDAY = '{PERIOD_END}' THEN CLOSEPRICE END) AS end_price
FROM ranked WHERE rn = 1
GROUP BY INNERCODE
ORDER BY INNERCODE
""")
    if data.empty:
        return {}
    data = data[(data.start_price > 0) & (data.end_price > 0)]
    return dict(zip(data.stock_innercode.astype(int), data.end_price / data.start_price - 1))


def load_industry_map(codes: list[int]) -> dict[int, str]:
    code_list = ",".join(str(int(x)) for x in sorted(set(codes)))
    data = run_sql(f"""
WITH stocks AS (
    SELECT INNERCODE, COMPANYCODE FROM secumain WHERE INNERCODE IN ({code_list})
), ordinary AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYCODE,
           ROW_NUMBER() OVER (PARTITION BY COMPANYCODE ORDER BY INFOPUBLDATE DESC, ID DESC) AS rn
    FROM lc_exgindustry
    WHERE STANDARD = 38 AND INFOPUBLDATE <= '{PERIOD_START}'
      AND (CANCELDATE IS NULL OR CANCELDATE > '{PERIOD_START}')
      AND COMPANYCODE IN (SELECT COMPANYCODE FROM stocks)
), stib AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYCODE,
           ROW_NUMBER() OVER (PARTITION BY COMPANYCODE
               ORDER BY EFFECTIVEDATE DESC, INFOPUBLDATE DESC, ID DESC) AS rn
    FROM lc_stibexgindustry
    WHERE STANDARD = 38 AND EFFECTIVEDATE <= '{PERIOD_START}'
      AND INFOPUBLDATE < '{AS_OF_DATE}'
      AND (CANCELDATE IS NULL OR CANCELDATE > '{PERIOD_START}')
      AND COMPANYCODE IN (SELECT COMPANYCODE FROM stocks)
)
SELECT s.INNERCODE AS stock_innercode,
       COALESCE(NULLIF(TRIM(o.FIRSTINDUSTRYCODE), ''), NULLIF(TRIM(t.FIRSTINDUSTRYCODE), '')) AS industry_code
FROM stocks s
LEFT JOIN ordinary o ON o.COMPANYCODE = s.COMPANYCODE AND o.rn = 1
LEFT JOIN stib t ON t.COMPANYCODE = s.COMPANYCODE AND t.rn = 1
ORDER BY s.INNERCODE
""")
    data = data.dropna(subset=["industry_code"])
    return dict(zip(data.stock_innercode.astype(int), data.industry_code.astype(str)))


def build_industry_profile(weights, returns, industries):
    data = weights.copy()
    data["stock_return"] = data.stock_innercode.map(returns)
    data["industry_code"] = data.stock_innercode.map(industries).replace("", np.nan)
    data = data.replace([np.inf, -np.inf], np.nan).dropna(
        subset=["stock_return", "industry_code", "weight_raw"]
    )
    data = data[data.weight_raw > 0]
    if data.empty:
        raise ValueError("No stocks with valid exact endpoints and industry")
    data["weighted_return"] = data.stock_return * data.weight_raw
    weight = data.groupby("industry_code").weight_raw.sum()
    returns_by_industry = data.groupby("industry_code").weighted_return.sum() / weight
    return (weight / weight.sum()).to_dict(), returns_by_industry.to_dict()


def brinson_three_term(fund_weight, fund_return, bench_weight, bench_return):
    portfolio = sum(w * fund_return[k] for k, w in fund_weight.items())
    benchmark = sum(w * bench_return[k] for k, w in bench_weight.items())
    allocation = selection = interaction = 0.0
    for industry in sorted(set(fund_weight) | set(bench_weight)):
        wp, wb = fund_weight.get(industry, 0.0), bench_weight.get(industry, 0.0)
        rb = bench_return[industry] if wb else benchmark
        allocation += (wp - wb) * (rb - benchmark)
        if wp:
            difference = fund_return[industry] - rb
            selection += wb * difference
            interaction += (wp - wb) * difference
    excess = portfolio - benchmark
    if not np.isclose(allocation + selection + interaction, excess, atol=1e-12, rtol=0):
        raise ValueError("Brinson contributions do not reconcile to portfolio excess")
    return [portfolio, benchmark, excess, allocation, selection, interaction]


def run_brinson() -> pd.DataFrame:
    fund, bench = resolve_meta()
    holdings, components = load_fund_holdings(fund), load_bench_components(bench)
    codes = holdings.stock_innercode.tolist() + components.stock_innercode.tolist()
    returns, industries = load_stock_returns(codes), load_industry_map(codes)
    wp, rp = build_industry_profile(holdings, returns, industries)
    wb, rb = build_industry_profile(components, returns, industries)
    values = brinson_three_term(wp, rp, wb, rb)
    return pd.DataFrame([[round(float(x) * 100, 2) for x in values]], columns=OUTPUT_COLUMNS)


def main() -> pd.DataFrame:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    load_benchmark_env()
    require_db_snapshot_id()
    result = run_brinson()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output_dir / "result.csv", index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    result = main() if SCRIPT_MODE else run_brinson()
