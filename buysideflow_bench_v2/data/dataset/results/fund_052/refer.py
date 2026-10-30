#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe

BASE_DIR = Path(__file__).resolve().parent

FUND_CODE = "014805"
INDEX_CODE = "399303"
MONITOR_START_DATE = "2025-01-01"
AS_OF_DATE = "2025-12-31"
VISIBLE_CUTOFF = "2026-01-01"
ROLLING_WINDOW = 30
JUMP_DAYS = 5
LOW_BETA = 0.2
HIGH_BETA = 0.6

OUTPUT_COLUMNS = [
    "基金代码",
    "基金简称",
    "是否触发",
    "首次触发日期",
    "触发日Beta",
    "监测期Beta下限",
    "监测期Beta上限",
]


def load_aligned_returns() -> tuple[pd.DataFrame, str]:
    sql = f"""
    WITH target_fund AS (
        SELECT
            fa.INNERCODE,
            CONCAT(fa.SECURITYCODE, '.OF') AS fund_code,
            COALESCE(sm.CHINAMEABBR, sm.SECUABBR, fa.SECURITYCODE) AS fund_name
        FROM mf_fundarchives fa
        JOIN secumain sm
          ON sm.INNERCODE = fa.INNERCODE
         AND sm.SECUCATEGORY = 8
        WHERE fa.SECURITYCODE = '{FUND_CODE}'
          AND fa.MAINCODE = fa.SECURITYCODE
          AND fa.ESTABLISHMENTDATE <= DATE('{AS_OF_DATE}')
          AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= DATE('{MONITOR_START_DATE}'))
        ORDER BY fa.INNERCODE
        LIMIT 1
    ),
    target_index AS (
        SELECT INNERCODE
        FROM secumain
        WHERE SECUCATEGORY = 4
          AND SECUCODE = '{INDEX_CODE}'
        ORDER BY INNERCODE
        LIMIT 1
    ),
    fund_nav_ranked AS (
        SELECT
            n.TRADINGDAY,
            n.NVRDAILYGROWTHRATE / 100 AS fund_ret,
            ROW_NUMBER() OVER (
                PARTITION BY n.TRADINGDAY
                ORDER BY n.INSERTTIME DESC, n.ID DESC
            ) AS rn
        FROM mf_fundnetvalueretrans n
        JOIN target_fund tf
          ON tf.INNERCODE = n.INNERCODE
        WHERE n.TRADINGDAY >= DATE('{MONITOR_START_DATE}')
          AND n.TRADINGDAY <= DATE('{AS_OF_DATE}')
          AND n.NVRDAILYGROWTHRATE IS NOT NULL
          AND n.INSERTTIME < DATE('{VISIBLE_CUTOFF}')
    ),
    fund_ret AS (
        SELECT
            TRADINGDAY,
            fund_ret
        FROM fund_nav_ranked
        WHERE rn = 1
    ),
    index_quote_ranked AS (
        SELECT
            q.TRADINGDAY,
            q.CLOSEPRICE / NULLIF(q.PREVCLOSEPRICE, 0) - 1 AS index_ret,
            ROW_NUMBER() OVER (
                PARTITION BY q.TRADINGDAY
                ORDER BY q.XGRQ DESC, q.ID DESC
            ) AS rn
        FROM qt_indexquote q
        JOIN target_index ti
          ON ti.INNERCODE = q.INNERCODE
        WHERE q.TRADINGDAY >= DATE('{MONITOR_START_DATE}')
          AND q.TRADINGDAY <= DATE('{AS_OF_DATE}')
          AND q.CLOSEPRICE IS NOT NULL
          AND q.PREVCLOSEPRICE IS NOT NULL
    ),
    index_ret AS (
        SELECT
            TRADINGDAY,
            index_ret
        FROM index_quote_ranked
        WHERE rn = 1
    )
    SELECT
        DATE_FORMAT(fr.TRADINGDAY, '%Y-%m-%d') AS trade_date,
        tf.fund_code,
        tf.fund_name,
        fr.fund_ret,
        ir.index_ret
    FROM fund_ret fr
    JOIN index_ret ir
      ON ir.TRADINGDAY = fr.TRADINGDAY
    CROSS JOIN target_fund tf
    ORDER BY fr.TRADINGDAY;
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        raise ValueError("未获取到基金与国证2000两边收益都可用的交易日")
    fund_code = str(df["fund_code"].iloc[0])
    return df, fund_code


def calculate_beta_series(df: pd.DataFrame) -> pd.DataFrame:
    data = df.copy()
    data["trade_date"] = pd.to_datetime(data["trade_date"], errors="coerce")
    data["fund_ret"] = pd.to_numeric(data["fund_ret"], errors="coerce")
    data["index_ret"] = pd.to_numeric(data["index_ret"], errors="coerce")
    data = data.dropna(subset=["trade_date", "fund_ret", "index_ret"]).sort_values("trade_date")
    data = data.reset_index(drop=True)
    if len(data) < ROLLING_WINDOW:
        raise ValueError(f"有效样本不足 {ROLLING_WINDOW} 个交易日，无法计算滚动 Beta")

    rolling_cov = data["fund_ret"].rolling(ROLLING_WINDOW).cov(data["index_ret"])
    rolling_var = data["index_ret"].rolling(ROLLING_WINDOW).var()
    data["beta"] = rolling_cov / rolling_var.replace(0, np.nan)
    return data.dropna(subset=["beta"]).reset_index(drop=True)


def build_result(aligned: pd.DataFrame, beta_df: pd.DataFrame) -> pd.DataFrame:
    beta_df = beta_df.copy()
    beta_df["prev_5d_min_beta"] = beta_df["beta"].shift(1).rolling(JUMP_DAYS, min_periods=1).min()
    drift = beta_df[
        (beta_df["beta"] > HIGH_BETA)
        & (beta_df["prev_5d_min_beta"] < LOW_BETA)
    ].copy()
    first_hit = drift.iloc[0] if not drift.empty else None

    fund_code = str(aligned["fund_code"].iloc[0])
    fund_name = str(aligned["fund_name"].iloc[0])
    result = pd.DataFrame(
        [
            {
                "基金代码": fund_code,
                "基金简称": fund_name,
                "是否触发": "是" if first_hit is not None else "否",
                "首次触发日期": "" if first_hit is None else first_hit["trade_date"].strftime("%Y-%m-%d"),
                "触发日Beta": np.nan if first_hit is None else round(float(first_hit["beta"]), 4),
                "监测期Beta下限": round(float(beta_df["beta"].min()), 4),
                "监测期Beta上限": round(float(beta_df["beta"].max()), 4),
            }
        ]
    )
    return result[OUTPUT_COLUMNS]


def main() -> pd.DataFrame:
    aligned, _ = load_aligned_returns()
    beta_df = calculate_beta_series(aligned)
    result = build_result(aligned, beta_df)
    result.to_csv(BASE_DIR / "result.csv", index=False, encoding="utf-8-sig", float_format="%.4f")
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
