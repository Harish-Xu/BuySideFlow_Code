#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


def _compute_summary(samples: pd.DataFrame) -> pd.DataFrame:
    required_cols = {"tradingday", "innercode", "ep_factor", "return_63d"}
    missing_cols = required_cols - set(samples.columns)
    if missing_cols:
        raise ValueError(f"SQL result missing columns: {sorted(missing_cols)}")

    df = samples.copy()
    df["tradingday"] = pd.to_datetime(df["tradingday"])
    df["ep_factor"] = pd.to_numeric(df["ep_factor"], errors="coerce")
    df["return_63d"] = pd.to_numeric(df["return_63d"], errors="coerce")
    df = df.dropna(subset=["tradingday", "ep_factor", "return_63d"])

    ic_rows: list[dict[str, float | pd.Timestamp | int]] = []
    for tradingday, group in df.groupby("tradingday", sort=True):
        if len(group) < 20:
            continue
        ep_rank = group["ep_factor"].rank(method="average", ascending=True)
        ret_rank = group["return_63d"].rank(method="average", ascending=True)
        if ep_rank.nunique() < 2 or ret_rank.nunique() < 2:
            continue
        ic_rows.append(
            {
                "tradingday": tradingday,
                "sample_size": len(group),
                "ic": ep_rank.corr(ret_rank),
            }
        )

    ic_df = pd.DataFrame(ic_rows).sort_values("tradingday")
    if ic_df.empty:
        raise ValueError("No valid IC periods with sample size >= 20")

    ic_std = ic_df["ic"].std(ddof=0)
    result = pd.DataFrame(
        [
            {
                "有效期数": int(len(ic_df)),
                "平均IC": round(float(ic_df["ic"].mean()), 4),
                "IC标准差": round(float(ic_std), 4) if pd.notna(ic_std) else None,
                "正IC比例": round(float((ic_df["ic"] > 0).mean() * 100), 2),
            }
        ]
    )
    return result


def main() -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    output_path = Path(__file__).with_name("result.csv")
    sql = sql_path.read_text(encoding="utf-8")
    samples = query_to_dataframe(sql, max_rows=0, timeout=600)
    if samples is None:
        raise ValueError("SQL execution returned None")
    result = _compute_summary(samples)
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(result.to_string(index=False) if not result.empty else "<empty result>")
    return result


if __name__ == "__main__":
    main()
