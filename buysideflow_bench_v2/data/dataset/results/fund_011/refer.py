#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


START_DAY = "2021-02-01"
END_DAY = "2024-03-31"
TARGET_CODE = "004958"
QUERY_CHUNK_SIZE = 300


FUND_POOL_SQL = f"""
WITH params AS (
    SELECT DATE('{START_DAY}') AS start_day,
           DATE('{END_DAY}') AS end_day,
           DATE_ADD(DATE('{END_DAY}'), INTERVAL 1 DAY) AS end_next_day
),
target_fund AS (
    SELECT fa.INNERCODE,
           fa.SECURITYCODE AS fund_code,
           COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, fa.SECURITYCODE) AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    WHERE fa.SECURITYCODE = '{TARGET_CODE}'
    LIMIT 1
),
fund_type_asof AS (
    SELECT t.INNERCODE,
           t.SECASSETCATCODE,
           ROW_NUMBER() OVER (
               PARTITION BY t.INNERCODE
               ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
           ) AS rn
    FROM mf_jyfundtype t
    JOIN params p
      ON t.EFFECTIVEDATE <= p.end_day
     AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.end_day)
    WHERE t.STANDARD = 75
      AND t.INSERTTIME < p.end_next_day
),
target_type AS (
    SELECT ft.SECASSETCATCODE
    FROM fund_type_asof ft
    JOIN target_fund tf
      ON tf.INNERCODE = ft.INNERCODE
    WHERE ft.rn = 1
),
peer_pool AS (
    SELECT fa.INNERCODE,
           fa.SECURITYCODE AS fund_code,
           COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, fa.SECURITYCODE) AS fund_name,
           CASE WHEN fa.SECURITYCODE = '{TARGET_CODE}' THEN 1 ELSE 0 END AS is_target
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN fund_type_asof ft
      ON ft.INNERCODE = fa.INNERCODE
     AND ft.rn = 1
    JOIN target_type tt
      ON tt.SECASSETCATCODE = ft.SECASSETCATCODE
    JOIN params p
      ON 1 = 1
    WHERE COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE < p.start_day
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.end_day)
)
SELECT INNERCODE,
       fund_code,
       fund_name,
       is_target
FROM peer_pool
ORDER BY INNERCODE;
"""


def fetch_interval_metrics(peer_pool: pd.DataFrame) -> pd.DataFrame:
    innercodes = sorted(
        pd.to_numeric(peer_pool["INNERCODE"], errors="coerce").dropna().astype(int).unique()
    )
    frames: list[pd.DataFrame] = []
    for start in range(0, len(innercodes), QUERY_CHUNK_SIZE):
        chunk = innercodes[start : start + QUERY_CHUNK_SIZE]
        code_sql = ", ".join(str(value) for value in chunk)
        frames.append(
            query_to_dataframe(
                f"""
WITH nav_ranked AS (
    SELECT
        n.INNERCODE,
        DATE(n.TRADINGDAY) AS trading_day,
        n.UNITNVRESTORED AS adj_nav,
        ROW_NUMBER() OVER (
            PARTITION BY n.INNERCODE, DATE(n.TRADINGDAY)
            ORDER BY n.TRADINGDAY DESC, n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans n FORCE INDEX (IX_MF_FUNDNETVALUERETRANS)
    WHERE n.INNERCODE IN ({code_sql})
      AND n.TRADINGDAY >= DATE('{START_DAY}')
      AND n.TRADINGDAY < DATE_ADD(DATE('{END_DAY}'), INTERVAL 1 DAY)
      AND n.UNITNVRESTORED IS NOT NULL
      AND n.INSERTTIME < DATE_ADD(DATE('{END_DAY}'), INTERVAL 1 DAY)
), sequenced AS (
    SELECT
        INNERCODE,
        trading_day,
        adj_nav,
        ROW_NUMBER() OVER (PARTITION BY INNERCODE ORDER BY trading_day) AS rn_asc,
        ROW_NUMBER() OVER (PARTITION BY INNERCODE ORDER BY trading_day DESC) AS rn_desc,
        MAX(adj_nav) OVER (
            PARTITION BY INNERCODE ORDER BY trading_day
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS running_peak
    FROM nav_ranked
    WHERE rn = 1
)
SELECT
    INNERCODE,
    MAX(CASE WHEN rn_asc = 1 THEN adj_nav END) AS first_nav,
    MAX(CASE WHEN rn_desc = 1 THEN adj_nav END) AS last_nav,
    MIN(adj_nav / NULLIF(running_peak, 0) - 1.0) AS max_drawdown,
    COUNT(*) AS nav_count
FROM sequenced
GROUP BY INNERCODE;
""",
                max_rows=0,
                timeout=120,
            )
        )
    metrics = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if metrics.empty:
        return metrics
    for column in ["first_nav", "last_nav", "max_drawdown", "nav_count"]:
        metrics[column] = pd.to_numeric(metrics[column], errors="coerce")
    metrics = metrics[(metrics["nav_count"] >= 2) & (metrics["first_nav"] > 0)].copy()
    metrics["cum_return"] = metrics["last_nav"] / metrics["first_nav"] - 1.0
    return metrics[["INNERCODE", "cum_return", "max_drawdown"]]


def main() -> pd.DataFrame:
    peer_pool = query_to_dataframe(FUND_POOL_SQL, max_rows=0, timeout=90)
    if peer_pool is None or peer_pool.empty:
        raise ValueError("未取到目标基金及同类基金池。")
    metric_df = peer_pool.merge(fetch_interval_metrics(peer_pool), on="INNERCODE", how="inner")
    metric_df = metric_df.dropna(subset=["cum_return", "max_drawdown"])
    if metric_df.empty:
        raise ValueError("有效同类样本为空，无法计算区间收益和最大回撤。")

    target_df = metric_df[metric_df["is_target"] == 1]
    if target_df.empty:
        raise ValueError("目标基金在区间内无可计算结果。")

    target = target_df.iloc[0]
    result = pd.DataFrame(
        [
            {
                "基金代码": target["fund_code"],
                "基金简称": target["fund_name"],
                "区间累计收益率": round(target["cum_return"] * 100, 2),
                "区间最大回撤": round(target["max_drawdown"] * 100, 2),
                "同类中位数累计收益率": round(metric_df["cum_return"].median() * 100, 2),
                "同类中位数最大回撤": round(metric_df["max_drawdown"].median() * 100, 2),
            }
        ]
    )

    output_path = Path(__file__).with_name("result.csv")
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
