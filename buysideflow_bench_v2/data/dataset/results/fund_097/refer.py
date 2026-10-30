#!/usr/bin/env python3
from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe

STAT_DATE = "2025-12-31"
VISIBLE_BY_DATE = "2026-01-01"
PERIOD_START = "2023-01-01"
PREV_PERIOD_START = "2022-01-01"
YEARS = (2023, 2024, 2025)
ANN_FACTOR = 252
TOP_N = 5
QUERY_CHUNK_SIZE = 350
OUTPUT_DIR = Path(__file__).resolve().parent


def pct2(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value) * 100:.2f}"


FUND_POOL_SQL = f"""
    WITH type_ranked AS (
        SELECT
            t.INNERCODE,
            t.SECASSETCATCODE,
            t.SECASSETCATNAME,
            ROW_NUMBER() OVER (
                PARTITION BY t.INNERCODE
                ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
            ) AS rn
        FROM mf_jyfundtype t
        WHERE t.STANDARD = 75
          AND t.EFFECTIVEDATE <= DATE('{STAT_DATE}')
          AND (t.CANCELDATE IS NULL OR t.CANCELDATE > DATE('{STAT_DATE}'))
          AND t.INSERTTIME < DATE_ADD(DATE('{VISIBLE_BY_DATE}'), INTERVAL 1 DAY)
          AND t.UPDATETIME < DATE_ADD(DATE('{VISIBLE_BY_DATE}'), INTERVAL 1 DAY)
    )
SELECT
    fa.INNERCODE,
    sm.SECUCODE,
    COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, sm.SECUCODE) AS fund_name
FROM mf_fundarchives fa
JOIN secumain sm
  ON sm.INNERCODE = fa.INNERCODE
JOIN type_ranked tr
  ON tr.INNERCODE = fa.INNERCODE
 AND tr.rn = 1
WHERE tr.SECASSETCATCODE = 1101
  AND COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
  AND fa.ESTABLISHMENTDATE <= DATE('2022-12-31')
  AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > DATE('{STAT_DATE}'))
ORDER BY sm.SECUCODE
"""


def fetch_metrics(fund_pool: pd.DataFrame) -> pd.DataFrame:
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
        n.NVRDAILYGROWTHRATE AS daily_growth_pct,
        ROW_NUMBER() OVER (
            PARTITION BY n.INNERCODE, DATE(n.TRADINGDAY)
            ORDER BY n.UPDATETIME DESC, n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans n FORCE INDEX (IX_MF_FUNDNETVALUERETRANS)
    WHERE n.INNERCODE IN ({code_sql})
      AND n.TRADINGDAY >= DATE('{PREV_PERIOD_START}')
      AND n.TRADINGDAY < DATE_ADD(DATE('{STAT_DATE}'), INTERVAL 1 DAY)
      AND n.UNITNVRESTORED IS NOT NULL
      AND n.INSERTTIME < DATE_ADD(DATE('{VISIBLE_BY_DATE}'), INTERVAL 1 DAY)
      AND n.UPDATETIME < DATE_ADD(DATE('{VISIBLE_BY_DATE}'), INTERVAL 1 DAY)
), year_ranked AS (
    SELECT
        INNERCODE,
        trading_day,
        adj_nav,
        daily_growth_pct,
        YEAR(trading_day) AS nav_year,
        ROW_NUMBER() OVER (
            PARTITION BY INNERCODE, YEAR(trading_day)
            ORDER BY trading_day DESC
        ) AS rn_year_desc
    FROM nav_ranked
    WHERE rn = 1
)
SELECT
    INNERCODE,
    STDDEV_POP(
        CASE WHEN trading_day >= DATE('{PERIOD_START}')
             THEN daily_growth_pct / 100.0 END
    ) AS daily_std,
    MAX(CASE WHEN nav_year = 2022 AND rn_year_desc = 1 THEN adj_nav END) AS nav_2022,
    MAX(CASE WHEN nav_year = 2023 AND rn_year_desc = 1 THEN adj_nav END) AS nav_2023,
    MAX(CASE WHEN nav_year = 2024 AND rn_year_desc = 1 THEN adj_nav END) AS nav_2024,
    MAX(CASE WHEN nav_year = 2025 AND rn_year_desc = 1 THEN adj_nav END) AS nav_2025
FROM year_ranked
GROUP BY INNERCODE;
""",
                max_rows=0,
                timeout=120,
            )
        )
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def build_result(fund_pool: pd.DataFrame, metrics: pd.DataFrame) -> pd.DataFrame:
    if metrics.empty:
        raise ValueError("未取到符合标准股票型主份额口径的 PIT 净值指标。")
    metric_columns = ["daily_std", "nav_2022", "nav_2023", "nav_2024", "nav_2025"]
    metrics[metric_columns] = metrics[metric_columns].apply(pd.to_numeric, errors="coerce")
    metrics = metrics.dropna(subset=metric_columns).copy()
    metrics["annual_volatility"] = metrics["daily_std"] * math.sqrt(ANN_FACTOR)
    metrics = metrics[
        (metrics["annual_volatility"] >= 0.20)
        & (metrics["annual_volatility"] <= 0.30)
    ].copy()
    for year in YEARS:
        metrics[f"return_{year}"] = metrics[f"nav_{year}"] / metrics[f"nav_{year - 1}"] - 1.0
    result = metrics.merge(fund_pool, on="INNERCODE", how="inner")
    result["avg_annual_return"] = result[[f"return_{year}" for year in YEARS]].mean(axis=1)
    result = result.sort_values(["avg_annual_return", "SECUCODE"], ascending=[False, True]).head(TOP_N)

    output = pd.DataFrame(
        {
            "基金代码": result["SECUCODE"],
            "基金简称": result["fund_name"],
            "年化波动率": result["annual_volatility"].map(pct2),
            "2023年收益率": result["return_2023"].map(pct2),
            "2024年收益率": result["return_2024"].map(pct2),
            "2025年收益率": result["return_2025"].map(pct2),
            "三年平均年度收益率": result["avg_annual_return"].map(pct2),
        }
    ).reset_index(drop=True)
    return output


def main() -> pd.DataFrame:
    fund_pool = query_to_dataframe(FUND_POOL_SQL, max_rows=0, timeout=90)
    if fund_pool.empty:
        raise ValueError("未取到符合标准股票型主份额口径的基金池。")
    output = build_result(fund_pool, fetch_metrics(fund_pool))
    output.to_csv(OUTPUT_DIR / "result.csv", index=False, encoding="utf-8-sig")
    return output


if __name__ == "__main__":
    print(main().to_string(index=False))
