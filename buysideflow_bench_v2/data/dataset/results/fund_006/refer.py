#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmark_paths import load_benchmark_env
from sweagent.text2sql.db_connector import query_to_dataframe
from sweagent.text2sql.provenance import require_db_snapshot_id


ANCHOR_DAY = "2025-12-19"
START_DAY = "2025-01-01"
CUTOFF_DAY = "2025-12-20"
MIN_OBS = 200
ES_LIMIT = -0.005
OUTPUT_COLUMNS = ["基金代码", "基金简称", "VaR95", "ES95"]

FUND_POOL_SQL = f"""
WITH eligible_shares AS (
    SELECT fa.INNERCODE, fa.SECURITYCODE AS fund_code, sm.SECUABBR AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain sm ON sm.INNERCODE = fa.INNERCODE AND sm.SECUCATEGORY = 8
    WHERE fa.MAINCODE = fa.SECURITYCODE AND fa.MAINCODE <> ''
      AND fa.TYPE IN (2, 3, 4, 7, 8)
      AND fa.FLOATTYPE IN (2, 3)
      AND fa.ESTABLISHMENTDATE <= '{ANCHOR_DAY}'
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= '{ANCHOR_DAY}')
), ranked_types AS (
    SELECT t.INNERCODE, t.SECASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS row_num
    FROM mf_jyfundtype t
    JOIN eligible_shares f ON f.INNERCODE = t.INNERCODE
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= '{ANCHOR_DAY}'
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > '{ANCHOR_DAY}')
      AND t.INSERTTIME < '{CUTOFF_DAY}'
)
SELECT f.INNERCODE, f.fund_code, f.fund_name
FROM eligible_shares f
JOIN ranked_types t ON t.INNERCODE = f.INNERCODE
WHERE t.row_num = 1 AND t.SECASSETCATCODE = 1301
ORDER BY f.fund_code
"""

RETURN_SQL_TEMPLATE = """
WITH ranked_returns AS (
    SELECT n.INNERCODE, DATE(n.TRADINGDAY) AS trade_date, n.NVRDAILYGROWTHRATE,
        ROW_NUMBER() OVER (
            PARTITION BY n.INNERCODE, DATE(n.TRADINGDAY)
            ORDER BY n.INSERTTIME DESC, n.ID DESC
        ) AS row_num
    FROM mf_fundnetvalueretrans n
    WHERE n.INNERCODE IN ({innercode_list})
      AND n.TRADINGDAY >= '{start_day}'
      AND n.TRADINGDAY < '{cutoff_day}'
      AND n.INSERTTIME < '{cutoff_day}'
)
SELECT r.INNERCODE, JSON_ARRAYAGG(r.NVRDAILYGROWTHRATE) AS daily_returns
FROM ranked_returns r
WHERE r.row_num = 1
  AND r.NVRDAILYGROWTHRATE IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM qt_tradingdaynew d
      WHERE d.SECUMARKET IN (83, 90) AND d.IFTRADINGDAY = 1
        AND d.TRADINGDATE = r.trade_date
  )
GROUP BY r.INNERCODE
"""


def fetch_fund_pool() -> pd.DataFrame:
    pool = query_to_dataframe(FUND_POOL_SQL, max_rows=0, timeout=300)
    if pool.empty:
        raise ValueError("未取到观察日存续的纯债型主份额。")
    pool = pool.copy()
    pool["INNERCODE"] = pd.to_numeric(pool["INNERCODE"], errors="raise").astype(int)
    if pool["INNERCODE"].duplicated().any() or pool["fund_code"].duplicated().any():
        raise ValueError("主份额基金池存在重复代码。")
    if pool[["fund_code", "fund_name"]].isna().any().any():
        raise ValueError("基金池缺少代码或简称。")
    return pool


def fetch_return_data(pool: pd.DataFrame) -> pd.DataFrame:
    sql = RETURN_SQL_TEMPLATE.format(
        innercode_list=",".join(str(int(code)) for code in pool["INNERCODE"]),
        start_day=START_DAY,
        cutoff_day=CUTOFF_DAY,
    )
    returns = query_to_dataframe(sql, max_rows=0, timeout=300)
    if returns.empty:
        raise ValueError("未取到观察日可得的年内日收益数据。")
    returns = returns.copy()
    returns["INNERCODE"] = pd.to_numeric(returns["INNERCODE"], errors="raise").astype(int)
    returns["daily_returns"] = returns["daily_returns"].map(json.loads)
    return returns.merge(pool, on="INNERCODE", how="inner", validate="one_to_one")


def calc_tail_risk(return_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for fund in return_df.itertuples(index=False):
        daily_ret = np.asarray(fund.daily_returns, dtype=float) / 100.0
        daily_ret = np.sort(daily_ret[np.isfinite(daily_ret)])
        if len(daily_ret) < MIN_OBS:
            continue
        var95 = float(np.quantile(daily_ret, 0.05, method="linear"))
        es95 = float(daily_ret[daily_ret <= var95].mean())
        if es95 < ES_LIMIT:
            rows.append({
                "基金代码": fund.fund_code, "基金简称": fund.fund_name,
                "VaR95": var95, "ES95": es95,
            })

    # Keep full precision through filtering and sorting; round only for display.
    result = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    result = result.sort_values(["ES95", "基金代码"]).reset_index(drop=True)
    return result.round({"VaR95": 6, "ES95": 6})


def main() -> pd.DataFrame:
    parser = argparse.ArgumentParser(description="纯债基金年内尾部风险预警")
    parser.add_argument("--output-path", type=Path, default=Path(__file__).with_name("result.csv"))
    args = parser.parse_args()
    load_benchmark_env()
    require_db_snapshot_id()
    output = calc_tail_risk(fetch_return_data(fetch_fund_pool()))
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.output_path, index=False, encoding="utf-8-sig", float_format="%.6f")
    print(output.to_string(index=False))
    return output


if __name__ == "__main__":
    main()
