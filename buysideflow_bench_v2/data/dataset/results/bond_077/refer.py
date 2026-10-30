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


CURVE_LABELS = {10: "国债", 195: "国开债"}
CURVE_ORDER = ["国债", "国开债"]
TENORS = [1, 10]
MONTH_END_FUNDING_DAYS = 5
RESULT_COLUMNS = [
    "基准日",
    "GC007变化",
    "FR007变化",
    "曲线",
    "期限",
    "第1个曲线日变化",
    "第5个曲线日变化",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="2025年月末资金面变化后的国债和国开债曲线反应")
    parser.add_argument("--output-path", default=str(Path(__file__).with_name("result.csv")))
    return parser.parse_args()


def load_raw_data() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    # Do not silently shift a five-day window by dropping unknown availability.
    unknown = query_to_dataframe(
        """
SELECT DATE(ENDDATE) AS obs_date
FROM bond_baseratereference
WHERE ENDDATE >= DATE('2025-01-01') AND ENDDATE < DATE('2026-01-01')
  AND GC007 IS NOT NULL AND FR007 IS NOT NULL AND INSERTTIME IS NULL
LIMIT 1
""",
        max_rows=1,
        timeout=600,
    )
    if unknown is None or not unknown.empty:
        raise ValueError("Funding publication availability is unresolved; cannot certify the month-end windows")
    raw = query_to_dataframe(sql, max_rows=0, timeout=600)
    if raw is None:
        raise ValueError("SQL execution returned None")
    return raw


def build_funding_windows(raw: pd.DataFrame) -> pd.DataFrame:
    funding = raw.loc[
        raw["row_type"] == "funding", ["obs_date", "gc007_pct", "fr007_pct"]
    ].copy()
    funding["obs_date"] = pd.to_datetime(funding["obs_date"], errors="coerce")
    funding["gc007_pct"] = pd.to_numeric(funding["gc007_pct"], errors="coerce")
    funding["fr007_pct"] = pd.to_numeric(funding["fr007_pct"], errors="coerce")
    funding = funding.dropna().sort_values("obs_date")
    funding["month"] = funding["obs_date"].dt.to_period("M")

    rows: list[dict[str, object]] = []
    for _, group in funding.groupby("month", sort=True):
        window = group.tail(MONTH_END_FUNDING_DAYS)
        if len(window) != MONTH_END_FUNDING_DAYS:
            continue
        first = window.iloc[0]
        last = window.iloc[-1]
        rows.append(
            {
                "base_date": pd.Timestamp(last["obs_date"]),
                "gc007_change_bp": (float(last["gc007_pct"]) - float(first["gc007_pct"])) * 100,
                "fr007_change_bp": (float(last["fr007_pct"]) - float(first["fr007_pct"])) * 100,
            }
        )
    return pd.DataFrame(rows)


def build_complete_curve_panel(raw: pd.DataFrame) -> pd.DataFrame:
    curve = raw.loc[
        raw["row_type"] == "curve",
        ["obs_date", "curve_code", "tenor_year", "yield_decimal"],
    ].copy()
    curve["obs_date"] = pd.to_datetime(curve["obs_date"], errors="coerce")
    curve["curve_code"] = pd.to_numeric(curve["curve_code"], errors="coerce")
    curve["tenor_year"] = pd.to_numeric(curve["tenor_year"], errors="coerce")
    curve["yield_decimal"] = pd.to_numeric(curve["yield_decimal"], errors="coerce")
    curve = curve.dropna().copy()
    curve["curve_label"] = curve["curve_code"].astype(int).map(CURVE_LABELS)
    curve["tenor_year"] = curve["tenor_year"].astype(int)
    curve = curve.dropna(subset=["curve_label"])

    required = pd.MultiIndex.from_tuples(
        [(curve_label, tenor) for curve_label in CURVE_ORDER for tenor in TENORS]
    )
    panel = curve.pivot_table(
        index="obs_date",
        columns=["curve_label", "tenor_year"],
        values="yield_decimal",
        aggfunc="last",
    ).reindex(columns=required)
    return panel.dropna(subset=required).sort_index()


def compute_result(funding_windows: pd.DataFrame, curve_panel: pd.DataFrame) -> pd.DataFrame:
    if funding_windows.empty or curve_panel.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    rows: list[dict[str, object]] = []
    curve_dates = list(curve_panel.index)
    date_to_pos = {pd.Timestamp(day): pos for pos, day in enumerate(curve_dates)}
    for _, window in funding_windows.sort_values("base_date").iterrows():
        base_date = pd.Timestamp(window["base_date"])
        base_pos = date_to_pos.get(base_date)
        if base_pos is None or base_pos + 5 >= len(curve_panel):
            continue

        base = curve_panel.iloc[base_pos]
        day_1 = curve_panel.iloc[base_pos + 1]
        day_5 = curve_panel.iloc[base_pos + 5]
        for curve_label in CURVE_ORDER:
            for tenor in TENORS:
                rows.append(
                    {
                        "基准日": base_date.strftime("%Y-%m-%d"),
                        "GC007变化": float(window["gc007_change_bp"]),
                        "FR007变化": float(window["fr007_change_bp"]),
                        "曲线": curve_label,
                        "期限": f"{tenor}Y",
                        "第1个曲线日变化": (
                            float(day_1[(curve_label, tenor)]) - float(base[(curve_label, tenor)])
                        ) * 10000,
                        "第5个曲线日变化": (
                            float(day_5[(curve_label, tenor)]) - float(base[(curve_label, tenor)])
                        ) * 10000,
                    }
                )
    return pd.DataFrame(rows, columns=RESULT_COLUMNS)


def main() -> None:
    args = parse_args()
    raw = load_raw_data()
    result = compute_result(build_funding_windows(raw), build_complete_curve_panel(raw))
    Path(args.output_path).parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output_path, index=False, encoding="utf-8-sig", float_format="%.2f")


if __name__ == "__main__":
    main()
