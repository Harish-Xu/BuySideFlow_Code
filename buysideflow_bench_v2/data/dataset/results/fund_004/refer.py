#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from sweagent.text2sql.db_connector import query_to_dataframe
except ImportError:
    from sweagent.text2sql.db_connector import query_to_dataframe


TRADING_DAYS_PER_YEAR = 252
MIN_DAILY_RETURN_COUNT = 200
TOP_FUND_COUNT = 100
OUTPUT_COMPANY_COUNT = 10
QUERY_CHUNK_SIZE = 350


FUND_POOL_SQL = """
WITH params AS (
    SELECT
        DATE('2025-12-31') AS anchor_day,
        DATE('2025-01-01') AS start_day
),
type_asof AS (
    SELECT
        t.INNERCODE,
        t.SECASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS rn
    FROM mf_jyfundtype t
    JOIN params p
      ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= p.anchor_day
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.anchor_day)
      AND t.INSERTTIME < DATE_ADD(p.anchor_day, INTERVAL 1 DAY)
),
active_equity_funds AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        COALESCE(sm.CHINAMEABBR, sm.SECUABBR, sm.CHINAME, fa.SECURITYCODE) AS fund_name,
        fa.INVESTADVISORCODE
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN type_asof ta
      ON ta.INNERCODE = fa.INNERCODE
     AND ta.rn = 1
    JOIN params p
      ON 1 = 1
    WHERE ta.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
      AND fa.INVESTMENTTYPE = 16
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= p.anchor_day
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.anchor_day)
),
advisor_info AS (
    SELECT
        INVESTADVISORCODE,
        MAX(INVESTADVISORNAME) AS INVESTADVISORNAME
    FROM mf_investadvisoroutline
    GROUP BY INVESTADVISORCODE
)
SELECT
    aef.INNERCODE,
    aef.fund_code,
    aef.fund_name,
    aef.INVESTADVISORCODE,
    ai.INVESTADVISORNAME
FROM active_equity_funds aef
LEFT JOIN advisor_info ai
  ON ai.INVESTADVISORCODE = aef.INVESTADVISORCODE
ORDER BY aef.fund_code
"""


def fetch_fund_metrics(fund_pool: pd.DataFrame) -> pd.DataFrame:
    innercodes = sorted(
        pd.to_numeric(fund_pool["INNERCODE"], errors="coerce").dropna().astype(int).unique()
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
      AND n.TRADINGDAY >= DATE('2025-01-01')
      AND n.TRADINGDAY < DATE('2026-01-01')
      AND n.UNITNVRESTORED IS NOT NULL
      AND n.INSERTTIME < DATE('2026-01-01')
), sequenced AS (
    SELECT
        INNERCODE,
        trading_day,
        adj_nav,
        ROW_NUMBER() OVER (PARTITION BY INNERCODE ORDER BY trading_day) AS rn_asc,
        ROW_NUMBER() OVER (PARTITION BY INNERCODE ORDER BY trading_day DESC) AS rn_desc,
        LAG(adj_nav) OVER (PARTITION BY INNERCODE ORDER BY trading_day) AS previous_nav,
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
    STDDEV_POP(
        CASE WHEN previous_nav > 0 THEN adj_nav / previous_nav - 1.0 END
    ) AS daily_std,
    SUM(CASE WHEN previous_nav > 0 THEN 1 ELSE 0 END) AS daily_return_count,
    MIN(adj_nav) AS min_nav
FROM sequenced
GROUP BY INNERCODE;
""",
                max_rows=0,
                timeout=90,
            )
        )
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> pd.DataFrame:
    fund_pool = query_to_dataframe(FUND_POOL_SQL, max_rows=0, timeout=90)
    if fund_pool.empty:
        raise ValueError("未取到 2025 年主动权益主份额基金池。")
    metrics = fetch_fund_metrics(fund_pool)
    if metrics.empty:
        raise ValueError("未取到 2025 年主动权益主份额基金净值指标。")
    numeric_columns = [
        "first_nav",
        "last_nav",
        "max_drawdown",
        "daily_std",
        "daily_return_count",
        "min_nav",
    ]
    metrics[numeric_columns] = metrics[numeric_columns].apply(pd.to_numeric, errors="coerce")
    metrics = metrics[
        (metrics["daily_return_count"] >= MIN_DAILY_RETURN_COUNT)
        & (metrics["min_nav"] > 0)
    ].copy()
    fund_metric_df = fund_pool.merge(metrics, on="INNERCODE", how="inner")
    if fund_metric_df.empty:
        raise ValueError("没有满足有效日收益不少于 200 个的主动权益主份额基金。")
    fund_metric_df["return_2025_pct"] = (
        fund_metric_df["last_nav"] / fund_metric_df["first_nav"] - 1.0
    ) * 100.0
    fund_metric_df["max_drawdown_pct"] = fund_metric_df["max_drawdown"] * 100.0
    fund_metric_df["annualized_volatility_pct"] = (
        fund_metric_df["daily_std"] * np.sqrt(TRADING_DAYS_PER_YEAR) * 100.0
    )
    fund_metric_df = fund_metric_df.rename(
        columns={"INVESTADVISORCODE": "advisor_code", "INVESTADVISORNAME": "advisor_name"}
    )

    fund_metric_df["return_rank"] = fund_metric_df["return_2025_pct"].rank(
        method="min",
        ascending=False,
    )
    top100_df = fund_metric_df[fund_metric_df["return_rank"] <= TOP_FUND_COUNT].copy()
    if top100_df.empty:
        raise ValueError("年度收益率 Top100 基金样本为空。")

    advisor_summary_df = (
        top100_df.groupby(["advisor_code", "advisor_name"], dropna=False, as_index=False)
        .agg(
            top100_fund_count=("INNERCODE", "count"),
            avg_max_drawdown_pct=("max_drawdown_pct", "mean"),
            avg_annualized_volatility_pct=("annualized_volatility_pct", "mean"),
            avg_return_2025_pct=("return_2025_pct", "mean"),
        )
        .sort_values(
            ["top100_fund_count", "avg_return_2025_pct", "advisor_name"],
            ascending=[False, False, True],
        )
        .head(OUTPUT_COMPANY_COUNT)
        .reset_index(drop=True)
    )

    result_df = advisor_summary_df.rename(
        columns={
            "advisor_name": "基金公司名称",
            "top100_fund_count": "入围 Top100 基金数",
            "avg_max_drawdown_pct": "平均最大回撤",
            "avg_annualized_volatility_pct": "平均年化波动率",
            "avg_return_2025_pct": "平均收益率",
        }
    )[
        [
            "基金公司名称",
            "入围 Top100 基金数",
            "平均最大回撤",
            "平均年化波动率",
            "平均收益率",
        ]
    ].copy()

    result_df["入围 Top100 基金数"] = result_df["入围 Top100 基金数"].astype(int)
    for col in ["平均最大回撤", "平均年化波动率", "平均收益率"]:
        result_df[col] = result_df[col].round(2)

    print(result_df.to_string(index=False))
    return result_df


if __name__ == "__main__":
    output_df = main()
    output_df.to_csv(
        Path(__file__).with_name("result.csv"),
        index=False,
        encoding="utf-8-sig",
        float_format="%.2f",
    )
