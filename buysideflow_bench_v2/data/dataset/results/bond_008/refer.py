#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


DEFAULT_AS_OF_DATE = "2025-06-30"
OUTPUT_COLUMNS = [
    "样本起始日期", "样本结束日期", "观察日10年期收益率(%)",
    "观察日1年期收益率(%)", "观察日期限利差(bp)", "年内日均利差(bp)",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="10Y-1Y 国债期限利差跟踪")
    parser.add_argument("--as-of-date", default=DEFAULT_AS_OF_DATE)
    parser.add_argument("--output-path")
    return parser.parse_args()


def build_sql(as_of_date: str) -> str:
    as_of_ts = pd.Timestamp(as_of_date).normalize()
    query_start = f"{as_of_ts.year}-01-01"
    cutoff = (as_of_ts + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    return f"""
    SELECT
        DATE(ENDDATE) AS trade_date,
        YEARSTOMATURITY,
        YIELD
    FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE CURVECODE = 10
      AND YIELDTYPECODE = 1
      AND YEARSTOMATURITY IN (1, 10)
      AND ENDDATE >= '{query_start}'
      AND ENDDATE < '{cutoff}'
      AND YIELD IS NOT NULL
    ORDER BY ENDDATE ASC, YEARSTOMATURITY ASC
    """


def prepare_data(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        raise ValueError("国债收益率曲线数据为空")

    data = df.copy()
    data["trade_date"] = pd.to_datetime(data["trade_date"], errors="raise").dt.normalize()
    data["YEARSTOMATURITY"] = pd.to_numeric(data["YEARSTOMATURITY"], errors="raise")
    data["YIELD"] = pd.to_numeric(data["YIELD"], errors="raise")

    duplicate_keys = data.duplicated(["trade_date", "YEARSTOMATURITY"], keep=False)
    if duplicate_keys.any():
        raise ValueError("同一曲线日和期限存在多条收益率记录")

    curve = data.pivot(index="trade_date", columns="YEARSTOMATURITY", values="YIELD")
    missing_tenors = {1, 10} - set(curve.columns)
    if missing_tenors:
        raise ValueError(f"缺少国债曲线期限：{sorted(missing_tenors)}")

    curve = curve[[1, 10]].dropna().rename(columns={1: "yield_1y", 10: "yield_10y"})
    if curve.empty:
        raise ValueError("没有1年期和10年期同日完整的国债收益率")

    curve["spread_10y_1y"] = curve["yield_10y"] - curve["yield_1y"]
    return curve.reset_index().sort_values("trade_date").reset_index(drop=True)


def build_output(as_of_date: str, curve: pd.DataFrame) -> pd.DataFrame:
    as_of_ts = pd.Timestamp(as_of_date).normalize()
    sample = curve[curve["trade_date"].between(as_of_ts.replace(month=1, day=1), as_of_ts)]
    current_rows = sample[sample["trade_date"] == as_of_ts]
    if len(current_rows) != 1:
        raise ValueError("观察日缺少1年期和10年期同日完整的国债收益率")
    current = current_rows.iloc[0]

    # YIELD stores decimal rates: multiply by 100 for percent, 10000 for bp.
    return pd.DataFrame([{
        "样本起始日期": f"{sample['trade_date'].min():%Y-%m-%d}",
        "样本结束日期": f"{sample['trade_date'].max():%Y-%m-%d}",
        "观察日10年期收益率(%)": round(float(current["yield_10y"]) * 100, 4),
        "观察日1年期收益率(%)": round(float(current["yield_1y"]) * 100, 4),
        "观察日期限利差(bp)": round(float(current["spread_10y_1y"]) * 10000, 2),
        "年内日均利差(bp)": round(float(sample["spread_10y_1y"].mean()) * 10000, 2),
    }], columns=OUTPUT_COLUMNS)


def main() -> pd.DataFrame:
    args = parse_args()
    as_of_date = pd.Timestamp(args.as_of_date).normalize().strftime("%Y-%m-%d")
    raw = query_to_dataframe(build_sql(as_of_date), max_rows=0, timeout=600)
    if raw is None:
        raise ValueError("SQL执行未返回结果")

    output = build_output(as_of_date, prepare_data(raw))
    output_path = Path(args.output_path) if args.output_path else Path(__file__).with_name("result.csv")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    formatted = output.copy()
    for column in OUTPUT_COLUMNS[2:4]:
        formatted[column] = formatted[column].map("{:.4f}".format)
    for column in OUTPUT_COLUMNS[4:]:
        formatted[column] = formatted[column].map("{:.2f}".format)
    formatted.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(output.to_string(index=False))

    return output


if __name__ == "__main__":
    main()
