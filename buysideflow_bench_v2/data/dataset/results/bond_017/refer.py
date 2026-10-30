#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np
import pandas as pd

from sweagent.text2sql.db_connector import query_to_dataframe

AS_OF_DATE = "2026-02-13"
CURVE_CODE = 10
YIELD_TYPE_CODE = 1
SOURCE_KNOTS = [0.5, 0.75, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0]
TARGET_TENORS = [2.0, 3.0, 5.0, 7.0, 10.0]
REFERENCE_TENOR = 1.0
HORIZONS = {"3M": 0.25, "6M": 0.5, "1Y": 1.0}
TENOR_LABELS = {1.0: "1Y", 2.0: "2Y", 3.0: "3Y", 5.0: "5Y", 7.0: "7Y", 10.0: "10Y"}


def load_curve() -> pd.DataFrame:
    knots = ", ".join(str(value) for value in SOURCE_KNOTS)
    sql = f"""
    SELECT YEARSTOMATURITY, YIELD
    FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE ENDDATE = DATE('{AS_OF_DATE}')
      AND CURVECODE = {CURVE_CODE}
      AND YIELDTYPECODE = {YIELD_TYPE_CODE}
      AND YEARSTOMATURITY IN ({knots})
      AND YIELD IS NOT NULL
    ORDER BY YEARSTOMATURITY
    """
    curve = query_to_dataframe(sql, max_rows=0, timeout=600)
    if curve.empty:
        raise ValueError("观察日国债收益率曲线为空")

    curve["YEARSTOMATURITY"] = pd.to_numeric(curve["YEARSTOMATURITY"], errors="coerce")
    curve["YIELD"] = pd.to_numeric(curve["YIELD"], errors="coerce")
    curve = curve.dropna().sort_values("YEARSTOMATURITY").reset_index(drop=True)
    if curve["YEARSTOMATURITY"].duplicated().any():
        raise ValueError("观察日国债收益率曲线存在重复期限")
    if set(curve["YEARSTOMATURITY"]) != set(SOURCE_KNOTS):
        raise ValueError("观察日国债收益率曲线节点不完整")
    return curve


def interpolate(curve_x: np.ndarray, curve_y: np.ndarray, tenor: float) -> float:
    return float(np.interp(tenor, curve_x, curve_y))


def modified_duration(tenor: float, yield_decimal: float) -> float:
    periods = int(round(tenor * 2))
    coupon = yield_decimal / 2
    present_value = 0.0
    weighted_time = 0.0
    for period in range(1, periods + 1):
        cashflow = coupon if period < periods else 1 + coupon
        discounted_cashflow = cashflow / (1 + yield_decimal / 2) ** period
        present_value += discounted_cashflow
        weighted_time += period / 2 * discounted_cashflow
    macaulay_duration = weighted_time / present_value
    return macaulay_duration / (1 + yield_decimal / 2)


def ride_return(
    curve_x: np.ndarray,
    curve_y: np.ndarray,
    tenor: float,
    horizon: float,
    duration: float,
) -> float:
    yield_now = interpolate(curve_x, curve_y, tenor)
    if tenor == horizon:
        return yield_now
    yield_rolled = interpolate(curve_x, curve_y, tenor - horizon)
    return yield_now + duration * (yield_now - yield_rolled) / horizon


def main() -> pd.DataFrame:
    curve = load_curve()
    curve_x = curve["YEARSTOMATURITY"].to_numpy(dtype=float)
    curve_y = curve["YIELD"].to_numpy(dtype=float)

    durations = {
        tenor: modified_duration(tenor, interpolate(curve_x, curve_y, tenor))
        for tenor in [REFERENCE_TENOR, *TARGET_TENORS]
    }
    reference_returns = {
        label: ride_return(
            curve_x,
            curve_y,
            REFERENCE_TENOR,
            horizon,
            durations[REFERENCE_TENOR],
        )
        for label, horizon in HORIZONS.items()
    }

    rows = []
    for tenor in TARGET_TENORS:
        yield_now = interpolate(curve_x, curve_y, tenor)
        row = {
            "tenor": TENOR_LABELS[tenor],
            "curve_yield_pct": round(yield_now * 100, 3),
            "modified_duration": round(durations[tenor], 3),
        }
        for label, horizon in HORIZONS.items():
            annualized_return = ride_return(
                curve_x, curve_y, tenor, horizon, durations[tenor]
            )
            duration_increment = durations[tenor] - durations[REFERENCE_TENOR]
            suffix = label.lower()
            row[f"ride_{suffix}_pct"] = round(annualized_return * 100, 3)
            row[f"excess_ride_per_duration_{suffix}"] = round(
                (annualized_return - reference_returns[label])
                / duration_increment
                * 100,
                3,
            )
        rows.append(row)

    columns = ["tenor", "curve_yield_pct", "modified_duration"]
    columns += [f"ride_{label.lower()}_pct" for label in HORIZONS]
    columns += [f"excess_ride_per_duration_{label.lower()}" for label in HORIZONS]
    return pd.DataFrame(rows, columns=columns)


if __name__ == "__main__":
    main().to_csv(
        Path(__file__).with_name("result.csv"),
        index=False,
        encoding="utf-8-sig",
        float_format="%.3f",
    )
