#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe  # noqa: E402


OUTPUT_COLUMNS = [
    "组合估值市值",
    "加权收益率",
    "加权修正久期",
    "加权凸性",
    "行业久期贡献占比",
]

REQUIRED_NUMERIC_COLUMNS = [
    "face_value_wan",
    "VALUEFULLPRICE",
    "VPYIELD",
    "VPADURATION",
    "VPCONVEXITY",
]


def format_pct(value: float) -> str:
    return f"{value:.4f}%"


def exposure_text(df: pd.DataFrame, group_col: str) -> str:
    if df.empty:
        return ""

    grouped = (
        df.groupby(group_col, dropna=False, as_index=False)["duration_exposure"]
        .sum()
        .rename(columns={"duration_exposure": "duration_exposure_sum"})
    )
    total_exposure = grouped["duration_exposure_sum"].sum()
    if total_exposure == 0:
        return ""

    grouped["exposure_pct"] = grouped["duration_exposure_sum"] / total_exposure * 100.0
    grouped[group_col] = grouped[group_col].fillna("未分类").astype(str)
    grouped = grouped.sort_values(["exposure_pct", group_col], ascending=[False, True])
    return "; ".join(
        f"{row[group_col]}:{row['exposure_pct']:.4f}%"
        for _, row in grouped.iterrows()
    )


def build_result(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        raise ValueError("未取得输入持仓")

    data = raw.copy()
    for col in REQUIRED_NUMERIC_COLUMNS:
        data[col] = pd.to_numeric(data[col], errors="coerce")

    valid = data.dropna(
        subset=["MAINCODE", "INNERCODE", *REQUIRED_NUMERIC_COLUMNS]
    ).copy()
    valid = valid[valid["face_value_wan"] > 0].copy()
    if valid.empty:
        raise ValueError("没有估值指标完整的有效持仓")

    valid["FIRSTINDUSTRYNAME"] = (
        valid["FIRSTINDUSTRYNAME"].fillna("未分类").astype(str).str.strip().replace("", "未分类")
    )

    # Valuation full price is per 100 face value. Input face values are in CNY
    # 10,000, so market value in CNY 100m is face_wan * full_price / 1,000,000.
    valid["market_value_100m"] = valid["face_value_wan"] * valid["VALUEFULLPRICE"] / 1_000_000.0
    total_market_value = valid["market_value_100m"].sum()
    valid["market_weight"] = valid["market_value_100m"] / total_market_value
    valid["duration_exposure"] = valid["market_weight"] * valid["VPADURATION"]

    weighted_yield = (valid["market_weight"] * valid["VPYIELD"]).sum()
    weighted_duration = valid["duration_exposure"].sum()
    weighted_convexity = (valid["market_weight"] * valid["VPCONVEXITY"]).sum()

    result = pd.DataFrame([{
        "组合估值市值": round(float(total_market_value), 4),
        "加权收益率": format_pct(float(weighted_yield)),
        "加权修正久期": round(float(weighted_duration), 4),
        "加权凸性": round(float(weighted_convexity), 4),
        "行业久期贡献占比": exposure_text(valid, "FIRSTINDUSTRYNAME"),
    }])
    return result[OUTPUT_COLUMNS]


def main() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    raw = query_to_dataframe(sql, max_rows=0, timeout=120)
    return build_result(raw)


def write_result(result: pd.DataFrame) -> pd.DataFrame:
    out_path = Path(__file__).with_name("result.csv")
    result.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    write_result(main())
