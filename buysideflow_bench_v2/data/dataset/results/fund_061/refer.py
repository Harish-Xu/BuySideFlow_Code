#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


OBSERVATION_DATE = "2026-02-09"
VISIBLE_CUTOFF = "2026-02-10"
TARGET_FUNDS = ["519697", "163415", "005562", "008272", "010169"]
QUERY_TIMEOUT_SECONDS = 600

OUTPUT_COLUMNS = [
    "观察日",
    "基金代码",
    "基金简称",
    "聚源二级分类",
    "近1周排名百分位",
    "近1月排名百分位",
    "近3月排名百分位",
]


def run_sql(sql: str) -> pd.DataFrame:
    df = query_to_dataframe(sql, max_rows=0, timeout=QUERY_TIMEOUT_SECONDS)
    if df is None:
        raise ValueError("SQL execution returned None")
    return df


def build_sql() -> str:
    target_union = " UNION ALL ".join(
        f"SELECT '{code}' AS fund_code" for code in TARGET_FUNDS
    )
    return f"""
WITH params AS (
    SELECT DATE('{OBSERVATION_DATE}') AS observation_date,
           TIMESTAMP('{VISIBLE_CUTOFF}') AS visible_cutoff
),
target_funds AS (
    {target_union}
),
asof_day AS (
    SELECT MAX(DATE(td.TRADINGDATE)) AS day_0
    FROM qt_tradingdaynew td
    JOIN params p
      ON DATE(td.TRADINGDATE) <= p.observation_date
    WHERE td.IFTRADINGDAY = 1
      AND td.SECUMARKET = 83
      AND td.XGRQ < p.visible_cutoff
),
trade_days AS (
    SELECT trading_day,
           ROW_NUMBER() OVER (ORDER BY trading_day DESC) AS rn
    FROM (
        SELECT DISTINCT DATE(td.TRADINGDATE) AS trading_day
        FROM qt_tradingdaynew td
        JOIN asof_day a
          ON DATE(td.TRADINGDATE) <= a.day_0
        JOIN params p
          ON td.XGRQ < p.visible_cutoff
        WHERE td.IFTRADINGDAY = 1
          AND td.SECUMARKET = 83
        ORDER BY trading_day DESC
        LIMIT 61
    ) d
),
anchor_days AS (
    SELECT MAX(CASE WHEN rn = 1 THEN trading_day END) AS day_0,
           MAX(CASE WHEN rn = 6 THEN trading_day END) AS day_5,
           MAX(CASE WHEN rn = 21 THEN trading_day END) AS day_20,
           MAX(CASE WHEN rn = 61 THEN trading_day END) AS day_60
    FROM trade_days
),
type_asof AS (
    SELECT t.INNERCODE,
           t.SECASSETCATCODE,
           t.SECASSETCATNAME,
           ROW_NUMBER() OVER (
               PARTITION BY t.INNERCODE
               ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
           ) AS rn
    FROM mf_jyfundtype t
    CROSS JOIN asof_day a
    CROSS JOIN params p
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= a.day_0
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > a.day_0)
      AND t.INSERTTIME < p.visible_cutoff
),
target_class AS (
    SELECT DISTINCT ta.SECASSETCATCODE
    FROM target_funds tf
    JOIN secumain sm
      ON sm.SECUCODE = tf.fund_code
     AND sm.SECUCATEGORY = 8
    JOIN type_asof ta
      ON ta.INNERCODE = sm.INNERCODE
     AND ta.rn = 1
),
peer_funds AS (
    SELECT sm.INNERCODE,
           sm.SECUCODE AS fund_code,
           COALESCE(sm.CHINAMEABBR, sm.SECUABBR, sm.SECUCODE) AS fund_name,
           ta.SECASSETCATCODE,
           ta.SECASSETCATNAME,
           CASE WHEN tf.fund_code IS NOT NULL THEN 1 ELSE 0 END AS is_target
    FROM secumain sm
    JOIN mf_fundarchives fa
      ON fa.INNERCODE = sm.INNERCODE
    JOIN type_asof ta
      ON ta.INNERCODE = sm.INNERCODE
     AND ta.rn = 1
    JOIN target_class tc
      ON tc.SECASSETCATCODE = ta.SECASSETCATCODE
    LEFT JOIN target_funds tf
      ON tf.fund_code = sm.SECUCODE
    CROSS JOIN anchor_days ad
    WHERE sm.SECUCATEGORY = 8
      AND fa.ESTABLISHMENTDATE <= ad.day_60
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > ad.day_0)
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND (fa.MAINCODE = fa.SECURITYCODE OR tf.fund_code IS NOT NULL)
),
nav_ranked AS (
    SELECT nv.INNERCODE,
           DATE(nv.TRADINGDAY) AS trading_day,
           nv.UNITNVRESTORED,
           ROW_NUMBER() OVER (
               PARTITION BY nv.INNERCODE, DATE(nv.TRADINGDAY)
               ORDER BY nv.UPDATETIME DESC, nv.INSERTTIME DESC, nv.ID DESC
           ) AS rn
    FROM mf_fundnetvalueretrans nv
    JOIN peer_funds pf
      ON pf.INNERCODE = nv.INNERCODE
    CROSS JOIN anchor_days ad
    CROSS JOIN params p
    WHERE DATE(nv.TRADINGDAY) IN (ad.day_0, ad.day_5, ad.day_20, ad.day_60)
      AND nv.UNITNVRESTORED IS NOT NULL
      AND nv.UNITNVRESTORED > 0
      AND nv.INSERTTIME < p.visible_cutoff
      AND nv.UPDATETIME < p.visible_cutoff
),
nav_points AS (
    SELECT pf.INNERCODE,
           pf.fund_code,
           pf.fund_name,
           pf.SECASSETCATCODE,
           pf.SECASSETCATNAME,
           pf.is_target,
           ad.day_0 AS observation_day,
           MAX(CASE WHEN n.trading_day = ad.day_0 THEN n.UNITNVRESTORED END) AS nv_0,
           MAX(CASE WHEN n.trading_day = ad.day_5 THEN n.UNITNVRESTORED END) AS nv_5,
           MAX(CASE WHEN n.trading_day = ad.day_20 THEN n.UNITNVRESTORED END) AS nv_20,
           MAX(CASE WHEN n.trading_day = ad.day_60 THEN n.UNITNVRESTORED END) AS nv_60
    FROM peer_funds pf
    CROSS JOIN anchor_days ad
    LEFT JOIN nav_ranked n
      ON n.INNERCODE = pf.INNERCODE
     AND n.rn = 1
    GROUP BY pf.INNERCODE, pf.fund_code, pf.fund_name, pf.SECASSETCATCODE,
             pf.SECASSETCATNAME, pf.is_target, ad.day_0
),
peer_returns AS (
    SELECT p.*,
           p.nv_0 / NULLIF(p.nv_5, 0) - 1 AS ret_1w,
           p.nv_0 / NULLIF(p.nv_20, 0) - 1 AS ret_1m,
           p.nv_0 / NULLIF(p.nv_60, 0) - 1 AS ret_3m
    FROM nav_points p
    WHERE p.nv_0 > 0
      AND p.nv_5 > 0
      AND p.nv_20 > 0
      AND p.nv_60 > 0
),
peer_rank AS (
    SELECT r.*,
           PERCENT_RANK() OVER (
               PARTITION BY r.SECASSETCATCODE ORDER BY r.ret_1w DESC
           ) * 100 AS rank_1w,
           PERCENT_RANK() OVER (
               PARTITION BY r.SECASSETCATCODE ORDER BY r.ret_1m DESC
           ) * 100 AS rank_1m,
           PERCENT_RANK() OVER (
               PARTITION BY r.SECASSETCATCODE ORDER BY r.ret_3m DESC
           ) * 100 AS rank_3m
    FROM peer_returns r
)
SELECT DATE_FORMAT(r.observation_day, '%Y-%m-%d') AS 观察日,
       CONCAT(r.fund_code, '.OF') AS 基金代码,
       r.fund_name AS 基金简称,
       r.SECASSETCATNAME AS 聚源二级分类,
       ROUND(r.rank_1w, 2) AS 近1周排名百分位,
       ROUND(r.rank_1m, 2) AS 近1月排名百分位,
       ROUND(r.rank_3m, 2) AS 近3月排名百分位
FROM peer_rank r
WHERE r.is_target = 1
  AND r.rank_1w > r.rank_1m
  AND r.rank_1m > r.rank_3m
ORDER BY r.fund_code;
"""


def run_rank_check() -> pd.DataFrame:
    df = run_sql(build_sql())
    if df.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    return df[OUTPUT_COLUMNS].reset_index(drop=True)


def main() -> pd.DataFrame:
    result = run_rank_check()
    output_path = Path(__file__).with_name("result.csv")
    result.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
