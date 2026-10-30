#!/usr/bin/env python3
from __future__ import annotations

import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe

AS_OF_DATE = "2026-01-16"
LOOKBACK_TRADING_DAYS = 252
RETURN_ANNUALIZATION_DAYS = 250
CORRELATION_THRESHOLD = 0.85
FUND_CODES = ("012349", "006328", "006603", "014673", "001691", "016495")
OUTPUT_DIR = Path(__file__).resolve().parent


def sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


def fmt4(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return ""
    return f"{float(value):.4f}"


def get_fund_info() -> pd.DataFrame:
    sql = f"""
    SELECT
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS fund_name,
        s.INNERCODE
    FROM secumain s
    WHERE s.SECUCODE IN {sql_in(FUND_CODES)}
    ORDER BY FIELD(s.SECUCODE, {", ".join(f"'{code}'" for code in FUND_CODES)}), s.INNERCODE
    """
    info = query_to_dataframe(sql, max_rows=0)
    if info.empty:
        raise ValueError("未取到基金基础信息，请核查基金代码。")
    info = info.drop_duplicates(subset=["SECUCODE"], keep="first").copy()
    missing = sorted(set(FUND_CODES) - set(info["SECUCODE"]))
    if missing:
        raise ValueError(f"以下基金代码未在 secumain 中取到: {missing}")
    return info


def get_pit_returns() -> pd.DataFrame:
    sql = f"""
    WITH code_pool AS (
        SELECT s.SECUCODE, s.INNERCODE
        FROM secumain s
        WHERE s.SECUCODE IN {sql_in(FUND_CODES)}
    ),
    nav_ranked AS (
        SELECT
            cp.SECUCODE,
            DATE(n.TRADINGDAY) AS trading_day,
            n.NVRDAILYGROWTHRATE,
            ROW_NUMBER() OVER (
                PARTITION BY cp.SECUCODE, DATE(n.TRADINGDAY)
                ORDER BY n.UPDATETIME DESC, n.INSERTTIME DESC, n.ID DESC
            ) AS rn
        FROM mf_fundnetvalueretrans n
        JOIN code_pool cp
          ON cp.INNERCODE = n.INNERCODE
        WHERE n.TRADINGDAY <= DATE('{AS_OF_DATE}')
          AND n.UNITNVRESTORED IS NOT NULL
          AND n.NVRDAILYGROWTHRATE IS NOT NULL
          AND n.INSERTTIME < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY)
          AND n.UPDATETIME < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY)
    )
    SELECT
        SECUCODE,
        trading_day,
        NVRDAILYGROWTHRATE
    FROM nav_ranked
    WHERE rn = 1
    ORDER BY trading_day, SECUCODE
    """
    raw = query_to_dataframe(sql, max_rows=0)
    if raw.empty:
        raise ValueError("未取到 PIT 复权净值日收益。")
    raw["trading_day"] = pd.to_datetime(raw["trading_day"])
    raw["NVRDAILYGROWTHRATE"] = pd.to_numeric(raw["NVRDAILYGROWTHRATE"], errors="coerce")
    return raw.dropna(subset=["NVRDAILYGROWTHRATE"])


def build_common_return_matrix(raw_returns: pd.DataFrame) -> pd.DataFrame:
    matrix = (
        raw_returns.pivot(index="trading_day", columns="SECUCODE", values="NVRDAILYGROWTHRATE")
        .reindex(columns=list(FUND_CODES))
        .sort_index()
        .dropna(how="any")
    )
    if len(matrix) < LOOKBACK_TRADING_DAYS:
        raise ValueError(
            f"六只基金共同可用复权日收益仅 {len(matrix)} 天，少于 {LOOKBACK_TRADING_DAYS} 天。"
        )
    return matrix.tail(LOOKBACK_TRADING_DAYS) / 100.0


def calmar_from_returns(return_series: pd.Series) -> dict[str, float]:
    wealth = (1.0 + return_series).cumprod()
    wealth_path = pd.concat(
        [pd.Series([1.0]), wealth.reset_index(drop=True)],
        ignore_index=True,
    )
    drawdown = wealth_path / wealth_path.cummax() - 1.0
    max_drawdown = float(drawdown.min())
    annual_return = float(wealth.iloc[-1] ** (RETURN_ANNUALIZATION_DAYS / len(return_series)) - 1.0)
    calmar = annual_return / abs(max_drawdown) if max_drawdown < 0 else np.nan
    return {
        "annual_return": annual_return,
        "max_drawdown": max_drawdown,
        "calmar": float(calmar) if not pd.isna(calmar) else np.nan,
    }


def better_fund(code_a: str, code_b: str, metrics: dict[str, dict[str, float]]) -> tuple[str, str]:
    metric_a = metrics[code_a]
    metric_b = metrics[code_b]
    rank_a = (
        -metric_a["calmar"] if not pd.isna(metric_a["calmar"]) else float("inf"),
        -metric_a["annual_return"],
        code_a,
    )
    rank_b = (
        -metric_b["calmar"] if not pd.isna(metric_b["calmar"]) else float("inf"),
        -metric_b["annual_return"],
        code_b,
    )
    return (code_a, code_b) if rank_a <= rank_b else (code_b, code_a)


def main() -> pd.DataFrame:
    info = get_fund_info()
    info_by_code = info.set_index("SECUCODE")

    returns = build_common_return_matrix(get_pit_returns())
    metrics = {code: calmar_from_returns(returns[code]) for code in FUND_CODES}

    trigger_counts = {code: 0 for code in FUND_CODES}
    high_corr_pairs: list[dict[str, str | float]] = []
    for code_a, code_b in combinations(FUND_CODES, 2):
        corr = float(returns[code_a].corr(returns[code_b]))
        if pd.isna(corr) or corr <= CORRELATION_THRESHOLD:
            continue
        _, lower_code = better_fund(code_a, code_b, metrics)
        trigger_counts[lower_code] += 1
        high_corr_pairs.append(
            {
                "pair_key": f"{code_a}-{code_b}",
                "基金A代码": code_a,
                "基金A简称": info_by_code.loc[code_a, "fund_name"],
                "基金B代码": code_b,
                "基金B简称": info_by_code.loc[code_b, "fund_name"],
                "相关系数": corr,
                "基金A Calmar": metrics[code_a]["calmar"],
                "基金B Calmar": metrics[code_b]["calmar"],
                "Calmar较低基金代码": lower_code,
                "Calmar较低基金简称": info_by_code.loc[lower_code, "fund_name"],
            }
        )

    high_corr_pairs.sort(key=lambda row: (-float(row["相关系数"]), str(row["pair_key"])))

    pair_output = []
    for row in high_corr_pairs:
        pair_output.append(
            {
                "高相关基金对": row["pair_key"],
                "相关系数": fmt4(row["相关系数"]),
                "基金A Calmar": fmt4(row["基金A Calmar"]),
                "基金B Calmar": fmt4(row["基金B Calmar"]),
                "Calmar较低基金代码": row["Calmar较低基金代码"],
                "较低方出现次数": trigger_counts[row["Calmar较低基金代码"]],
            }
        )

    result_df = pd.DataFrame(pair_output)
    result_df.to_csv(OUTPUT_DIR / "result.csv", index=False, encoding="utf-8-sig")
    return result_df


if __name__ == "__main__":
    pd.set_option("display.max_columns", None)
    print(main().to_string(index=False))
