#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


START_MONTH = "2023-01"
END_MONTH = "2024-12"

def build_monthly_panel(raw: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    if raw.empty:
        raise RuntimeError("SQL returned no daily observations")

    raw = raw.copy()
    raw["obs_date"] = pd.to_datetime(raw["obs_date"])
    raw["metric_value"] = pd.to_numeric(raw["metric_value"], errors="coerce")
    daily = (
        raw.pivot_table(
            index="obs_date",
            columns="series_name",
            values="metric_value",
            aggfunc="last",
        )
        .dropna(subset=["cn10_pct", "us10_pct", "usdcny_mid"])
        .reset_index()
    )
    if daily.empty:
        raise RuntimeError("No common daily observations across China 10Y, US 10Y, and USD/CNY mid")

    daily["月份"] = daily["obs_date"].dt.strftime("%Y-%m")
    monthly = (
        daily.sort_values("obs_date")
        .groupby("月份", as_index=False)
        .tail(1)
        .sort_values("obs_date")
        .reset_index(drop=True)
    )

    output_months = list(pd.period_range(START_MONTH, END_MONTH, freq="M").astype(str))
    missing = sorted(set(output_months) - set(monthly["月份"]))
    if missing:
        raise RuntimeError(f"Missing common monthly observations: {missing}")

    monthly = monthly[monthly["月份"].isin(output_months)].copy()
    monthly["中美10年利差(bp)"] = (monthly["us10_pct"] - monthly["cn10_pct"]) * 100
    monthly["利差变动(bp)"] = monthly["中美10年利差(bp)"].diff()
    monthly["中间价变动"] = monthly["usdcny_mid"].diff()
    output = monthly[(monthly["月份"] >= START_MONTH) & (monthly["月份"] <= END_MONTH)].copy()
    corr_sample = output.loc[output["月份"] > START_MONTH, ["利差变动(bp)", "中间价变动"]].dropna()
    corr = corr_sample.corr(method="pearson").iloc[0, 1]
    if pd.isna(corr):
        raise RuntimeError("Pearson correlation is undefined for the monthly changes")
    return output.reset_index(drop=True), float(corr)


def main() -> pd.DataFrame:
    base_dir = Path(__file__).resolve().parent
    sql = (base_dir / "refer.sql").read_text(encoding="utf-8")
    raw = query_to_dataframe(sql, max_rows=0, timeout=300)
    if raw is None:
        raise RuntimeError("SQL returned None")

    monthly, corr = build_monthly_panel(raw)
    if len(monthly) != 24:
        raise RuntimeError(f"Expected 24 monthly observations, got {len(monthly)}")
    result = pd.DataFrame([{"Pearson相关系数": f"{corr:.3f}"}])
    result.to_csv(base_dir / "result.csv", index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
