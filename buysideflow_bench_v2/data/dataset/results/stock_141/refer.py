#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


def main() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None:
        raise ValueError("SQL execution returned None")

    # 按行业分组计算统计量
    def pct(vals: pd.Series, q: float) -> float:
        """线性插值分位数 (MySQL PERCENTILE_CONT 语义)"""
        ordered = vals.dropna().sort_values().reset_index(drop=True)
        n = len(ordered)
        if n == 0:
            return float("nan")
        # 目标 rank = 1 + q * (n - 1)
        rank = 1 + q * (n - 1)
        lo = int(rank) - 1  # 0-indexed
        hi = lo + 1
        frac = rank - int(rank)
        if hi >= n:
            return float(ordered.iloc[lo])
        return float(ordered.iloc[lo] * (1 - frac) + ordered.iloc[hi] * frac)

    grouped = df.groupby("申万一级行业名称", sort=False)["毛利率"]
    result = pd.DataFrame({
        "申万一级行业名称": grouped.first().index,
        "样本数": grouped.count().values,
        "毛利率中位数": grouped.apply(lambda x: pct(x, 0.50)).values,
        "毛利率均值": grouped.mean().values,
        "25%分位数": grouped.apply(lambda x: pct(x, 0.25)).values,
        "75%分位数": grouped.apply(lambda x: pct(x, 0.75)).values,
    })

    # 保留样本数 >= 10
    result = result[result["样本数"] >= 10].reset_index(drop=True)
    # 按毛利率中位数降序
    result = result.sort_values("毛利率中位数", ascending=False).reset_index(drop=True)

    result.to_csv(output_path.with_name("comparison_raw.csv"), index=False, encoding="utf-8-sig")
    displayed = ["毛利率中位数", "毛利率均值", "25%分位数", "75%分位数"]
    result[displayed] = result[displayed].round(2)

    result.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    main()
