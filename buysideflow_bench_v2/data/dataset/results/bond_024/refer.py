#!/usr/bin/env python3
from __future__ import annotations

from bisect import bisect_left
from decimal import Decimal
import re
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import get_connection

AS_OF_DATE = "2025-12-31"
TOP_N = 10
RESULT_COLUMNS = ["债券代码", "证券简称", "行权利差(bp)", "到期利差(bp)", "利差差值(bp)"]


def load_sql_blocks() -> dict[str, str]:
    sql_text = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    matches = list(re.finditer(r"^-- name:\s*(\w+)\s*$", sql_text, flags=re.MULTILINE))
    return {
        match.group(1): sql_text[match.end():matches[idx + 1].start() if idx + 1 < len(matches) else len(sql_text)].strip().rstrip(";")
        for idx, match in enumerate(matches)
    }


def render_sql(template: str, **params: str) -> str:
    for key, value in params.items():
        template = template.replace(f"{{{{{key}}}}}", value)
    return template


def fetch_dataframe(blocks: dict[str, str], block_name: str) -> pd.DataFrame:
    sql = render_sql(blocks[block_name], as_of_date=AS_OF_DATE)
    # The raw cursor preserves database DECIMAL precision through interpolation and ranking.
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=180000")
            cursor.execute(sql)
            return pd.DataFrame(cursor.fetchall(), columns=[col[0] for col in cursor.description])
    finally:
        connection.close()


def decimal_value(value) -> Decimal | None:
    if pd.isna(value):
        return None
    number = Decimal(str(value))
    return number if number.is_finite() else None


def prepare_curve(curve: pd.DataFrame) -> tuple[list[Decimal], list[Decimal]]:
    points = {}
    for row in curve.to_dict("records"):
        tenor, value = decimal_value(row["maturity_year"]), decimal_value(row["yield_pct"])
        if tenor is not None and value is not None:
            points[tenor] = value
    if not points:
        raise ValueError("Treasury curve has no valid tenor/yield rows")
    tenors = sorted(points)
    return tenors, [points[t] for t in tenors]


def interpolate_curve(tenors, yields_pct, maturity: pd.Series) -> pd.Series:
    tenors = [Decimal(str(t)) for t in tenors]
    yields_pct = [Decimal(str(y)) for y in yields_pct]
    values = []
    for raw in maturity:
        t = decimal_value(raw)
        if t is None or t < tenors[0] or t > tenors[-1]:
            values.append(None)
            continue
        right = bisect_left(tenors, t)
        if tenors[right] == t:
            values.append(yields_pct[right])
        else:
            left = right - 1
            values.append(yields_pct[left] + (yields_pct[right] - yields_pct[left])
                          * (t - tenors[left]) / (tenors[right] - tenors[left]))
    return pd.Series(values, index=maturity.index, dtype=object)


def calculate_spreads(valuations: pd.DataFrame, curve: pd.DataFrame) -> pd.DataFrame:
    if valuations.empty:
        return pd.DataFrame()
    data = valuations.copy()
    numeric_cols = ["exercise_yield_pct", "exercise_remaining_years", "maturity_yield_pct", "maturity_remaining_years"]
    for col in numeric_cols:
        data[col] = data[col].map(decimal_value)
    data = data.dropna(subset=["bond_code", "bond_name", *numeric_cols]).copy()
    data = data.loc[data["exercise_remaining_years"].map(lambda t: Decimal(1) <= t <= Decimal(5))].copy()
    if data.empty:
        return data
    tenors, yields_pct = prepare_curve(curve)
    data["exercise_benchmark_pct"] = interpolate_curve(tenors, yields_pct, data["exercise_remaining_years"])
    data["maturity_benchmark_pct"] = interpolate_curve(tenors, yields_pct, data["maturity_remaining_years"])
    data = data.dropna(subset=["exercise_benchmark_pct", "maturity_benchmark_pct"]).copy()
    data["exercise_spread_bp"] = (data["exercise_yield_pct"] - data["exercise_benchmark_pct"]) * Decimal(100)
    data["maturity_spread_bp"] = (data["maturity_yield_pct"] - data["maturity_benchmark_pct"]) * Decimal(100)
    data["spread_diff_bp"] = data["exercise_spread_bp"] - data["maturity_spread_bp"]
    data["abs_diff_bp"] = data["spread_diff_bp"].abs()
    data["bond_code"] = data["bond_code"].astype(str)
    return data.sort_values(["abs_diff_bp", "bond_code"], ascending=[False, True], kind="mergesort")


def build_result(valuations: pd.DataFrame, curve: pd.DataFrame) -> pd.DataFrame:
    selected = calculate_spreads(valuations, curve).head(TOP_N)
    if selected.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    return pd.DataFrame({
        "债券代码": selected["bond_code"],
        "证券简称": selected["bond_name"],
        "行权利差(bp)": selected["exercise_spread_bp"].map(lambda x: f"{x:.2f}"),
        "到期利差(bp)": selected["maturity_spread_bp"].map(lambda x: f"{x:.2f}"),
        "利差差值(bp)": selected["spread_diff_bp"].map(lambda x: f"{x:.2f}"),
    }, columns=RESULT_COLUMNS).reset_index(drop=True)


def main() -> pd.DataFrame:
    blocks = load_sql_blocks()
    result = build_result(fetch_dataframe(blocks, "valuation_rows"), fetch_dataframe(blocks, "treasury_curve"))
    result.to_csv(Path(__file__).with_name("result.csv"), index=False, encoding="utf-8-sig")
    return result


if __name__ == "__main__":
    main()
