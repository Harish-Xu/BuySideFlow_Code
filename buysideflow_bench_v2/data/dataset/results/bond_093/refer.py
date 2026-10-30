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


START_DATE = pd.Timestamp("2025-03-31")
END_DATE = pd.Timestamp("2025-06-30")
CURVE_CODE = 10
YIELD_TYPE_CODE = 1

OUTPUT_COLUMNS = [
    "纳入债券数",
    "剔除债券数",
    "组合区间收益(%)",
    "carry(%)",
    "roll-down(%)",
    "曲线变动(%)",
    "利差变动(%)",
    "择券(%)",
    "最大贡献来源",
    "剔除说明",
]

COMPONENT_LABELS = {
    "carry": "carry",
    "roll_down": "roll-down",
    "curve_change": "曲线变动",
    "spread_change": "利差变动",
    "security_selection": "择券",
}


def _query(sql: str, *, timeout: int = 300) -> pd.DataFrame:
    df = query_to_dataframe(sql, max_rows=0, timeout=timeout)
    if df is None:
        raise ValueError("SQL execution returned None")
    return df


def load_holdings_valuation() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    df = _query(sql, timeout=300)
    if df.empty:
        return df

    df.columns = [col.lower() for col in df.columns]
    numeric_cols = [
        "face_value_wan",
        "maincode",
        "innercode",
        "start_full_price",
        "start_yield_pct",
        "start_modified_duration",
        "start_convexity",
        "start_remaining_years",
        "end_full_price",
        "end_yield_pct",
        "end_modified_duration",
        "end_convexity",
        "end_remaining_years",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_government_curve() -> pd.DataFrame:
    sql = f"""
    WITH raw_curve AS (
        SELECT
            DATE(ENDDATE) AS curve_date,
            YEARSTOMATURITY AS tenor_years,
            YIELD AS curve_yield,
            UPDATETIME,
            ID,
            ROW_NUMBER() OVER (
                PARTITION BY DATE(ENDDATE), YEARSTOMATURITY
                ORDER BY UPDATETIME DESC, ID DESC
            ) AS rn
        FROM bond_cbyieldcurve
        WHERE ENDDATE IN (DATE('{START_DATE.date()}'), DATE('{END_DATE.date()}'))
          AND CURVECODE = {CURVE_CODE}
          AND YIELDTYPECODE = {YIELD_TYPE_CODE}
          AND YEARSTOMATURITY IS NOT NULL
          AND YIELD IS NOT NULL
          AND (
                (ENDDATE = DATE('{START_DATE.date()}') AND UPDATETIME < TIMESTAMP('2025-04-01 00:00:00'))
             OR (ENDDATE = DATE('{END_DATE.date()}') AND UPDATETIME < TIMESTAMP('2025-07-01 00:00:00'))
          )
    )
    SELECT curve_date, tenor_years, curve_yield
    FROM raw_curve
    WHERE rn = 1
    ORDER BY curve_date, tenor_years;
    """
    curve = _query(sql, timeout=300)
    if curve.empty:
        return curve
    curve.columns = [col.lower() for col in curve.columns]
    curve["curve_date"] = pd.to_datetime(curve["curve_date"], errors="coerce")
    curve["tenor_years"] = pd.to_numeric(curve["tenor_years"], errors="coerce")
    curve["curve_yield"] = pd.to_numeric(curve["curve_yield"], errors="coerce")
    curve = curve.dropna(subset=["curve_date", "tenor_years", "curve_yield"])
    return curve.reset_index(drop=True)


def interpolate_curve(curve: pd.DataFrame, curve_date: pd.Timestamp, tenor: float) -> float:
    one_day = curve[curve["curve_date"] == curve_date].sort_values("tenor_years")
    if one_day.empty or pd.isna(tenor):
        raise ValueError(f"缺少 {curve_date.date()} 曲线数据")

    xs = one_day["tenor_years"].astype(float).to_numpy()
    # bond_cbyieldcurve.YIELD is stored as a decimal yield, e.g. 0.016 means 1.6%.
    ys = one_day["curve_yield"].astype(float).to_numpy()
    if tenor < xs.min() or tenor > xs.max():
        raise ValueError(f"{curve_date.date()} 曲线不能覆盖期限 {tenor:.4f} 年")
    return float(np.interp(float(tenor), xs, ys))


def missing_reasons(row: pd.Series) -> list[str]:
    reasons: list[str] = []
    if pd.isna(row.get("maincode")) or pd.isna(row.get("innercode")):
        reasons.append("未匹配债券代码")
    if pd.isna(row.get("face_value_wan")) or row.get("face_value_wan", 0) <= 0:
        reasons.append("面值无效")
    required_start = [
        ("start_full_price", "缺期初估值全价"),
        ("start_yield_pct", "缺期初估值收益率"),
        ("start_modified_duration", "缺期初修正久期"),
        ("start_remaining_years", "缺期初剩余期限"),
    ]
    required_end = [
        ("end_full_price", "缺期末估值全价"),
        ("end_yield_pct", "缺期末估值收益率"),
        ("end_remaining_years", "缺期末剩余期限"),
    ]
    for col, label in required_start + required_end:
        if pd.isna(row.get(col)):
            reasons.append(label)
    return reasons


def build_bond_attribution(raw: pd.DataFrame, curve: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if raw.empty:
        return pd.DataFrame(), raw.copy()

    data = raw.copy()
    data["missing_reason"] = data.apply(lambda row: "、".join(missing_reasons(row)), axis=1)
    candidates = data[data["missing_reason"] == ""].copy()
    excluded = data[data["missing_reason"] != ""].copy()

    if candidates.empty:
        return candidates, excluded

    rows: list[dict[str, float | str]] = []
    h = (END_DATE - START_DATE).days / 365.0
    for _, row in candidates.iterrows():
        g0_t0 = interpolate_curve(curve, START_DATE, float(row["start_remaining_years"]))
        g0_t1 = interpolate_curve(curve, START_DATE, float(row["end_remaining_years"]))
        g1_t1 = interpolate_curve(curve, END_DATE, float(row["end_remaining_years"]))

        y0 = float(row["start_yield_pct"]) / 100.0
        y1 = float(row["end_yield_pct"]) / 100.0
        d0 = float(row["start_modified_duration"])
        actual_return = float(row["end_full_price"]) / float(row["start_full_price"]) - 1.0
        carry = y0 * h
        roll_down = -d0 * (g0_t1 - g0_t0)
        curve_change = -d0 * (g1_t1 - g0_t1)
        spread_change = -d0 * ((y1 - g1_t1) - (y0 - g0_t0))
        security_selection = actual_return - carry - roll_down - curve_change - spread_change
        start_market_value_100m = (
            float(row["face_value_wan"]) * float(row["start_full_price"]) / 1_000_000.0
        )

        rows.append(
            {
                "bond_code": str(row["bond_code"]),
                "start_market_value_100m": start_market_value_100m,
                "actual_return": actual_return,
                "carry": carry,
                "roll_down": roll_down,
                "curve_change": curve_change,
                "spread_change": spread_change,
                "security_selection": security_selection,
                "g0_t0": g0_t0,
                "g0_t1": g0_t1,
                "g1_t1": g1_t1,
            }
        )

    attr = pd.DataFrame(rows)
    total_mv = attr["start_market_value_100m"].sum()
    attr["weight"] = attr["start_market_value_100m"] / total_mv
    return attr, excluded


def pct4(value: float) -> float:
    return round(float(value) * 100.0, 4)


def build_result(raw: pd.DataFrame, curve: pd.DataFrame) -> pd.DataFrame:
    attr, excluded = build_bond_attribution(raw, curve)
    if attr.empty:
        excluded_desc = "无输入持仓" if raw.empty else "; ".join(
            f"{row['bond_code']}:{row['missing_reason']}" for _, row in excluded.iterrows()
        )
        return pd.DataFrame(
            [{
                "纳入债券数": 0,
                "剔除债券数": int(len(excluded)),
                "组合区间收益(%)": 0.0,
                "carry(%)": 0.0,
                "roll-down(%)": 0.0,
                "曲线变动(%)": 0.0,
                "利差变动(%)": 0.0,
                "择券(%)": 0.0,
                "最大贡献来源": "",
                "剔除说明": excluded_desc,
            }],
            columns=OUTPUT_COLUMNS,
        )

    components = {
        "actual_return": float((attr["weight"] * attr["actual_return"]).sum()),
        "carry": float((attr["weight"] * attr["carry"]).sum()),
        "roll_down": float((attr["weight"] * attr["roll_down"]).sum()),
        "curve_change": float((attr["weight"] * attr["curve_change"]).sum()),
        "spread_change": float((attr["weight"] * attr["spread_change"]).sum()),
        "security_selection": float((attr["weight"] * attr["security_selection"]).sum()),
    }
    largest_key = max(
        ["carry", "roll_down", "curve_change", "spread_change", "security_selection"],
        key=lambda key: components[key],
    )
    excluded_desc = "无"
    if not excluded.empty:
        excluded_desc = "; ".join(
            f"{row['bond_code']}:{row['missing_reason']}" for _, row in excluded.iterrows()
        )

    return pd.DataFrame(
        [{
            "纳入债券数": int(len(attr)),
            "剔除债券数": int(len(excluded)),
            "组合区间收益(%)": pct4(components["actual_return"]),
            "carry(%)": pct4(components["carry"]),
            "roll-down(%)": pct4(components["roll_down"]),
            "曲线变动(%)": pct4(components["curve_change"]),
            "利差变动(%)": pct4(components["spread_change"]),
            "择券(%)": pct4(components["security_selection"]),
            "最大贡献来源": COMPONENT_LABELS[largest_key],
            "剔除说明": excluded_desc,
        }],
        columns=OUTPUT_COLUMNS,
    )


def main() -> pd.DataFrame:
    raw = load_holdings_valuation()
    curve = load_government_curve()
    return build_result(raw, curve)


def write_result(result: pd.DataFrame) -> pd.DataFrame:
    out_path = Path(__file__).with_name("result.csv")
    result.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    write_result(main())
