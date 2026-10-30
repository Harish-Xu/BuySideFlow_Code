#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = "2025-09-29"
NEXT_DAY_TS = "2025-09-30 00:00:00"
CHUNK_SIZE = 1000

RESULT_COLUMNS = [
    "方向",
    "ABS代码",
    "简称",
    "评级",
    "剩余期限",
    "估值收益率",
    "曲线收益率",
    "偏离值",
]

CURVE_MAP: dict[str, int] = {
    "AAA": 330,
    "AA+": 331,
    "AA": 332,
    "AA-": 333,
}


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def sql_list(values) -> str:
    return ",".join(str(int(v)) for v in values)


def chunks(values, size: int = CHUNK_SIZE):
    values = sorted({int(v) for v in values if pd.notna(v)})
    for i in range(0, len(values), size):
        yield values[i : i + size]


def normalize_rating(value: object) -> str | None:
    if pd.isna(value):
        return None
    rating = str(value).strip().upper()
    rating = rating.replace("＋", "+")
    rating = rating.replace("（", "(").replace("）", ")")
    rating = re.sub(r"\s+", "", rating)
    rating = rating.replace("SF", "")
    rating = rating.replace("(EXP)", "")
    rating = rating.replace("稳定", "")
    rating = rating.strip()
    valid = {"AAA", "AAA-", "AA+", "AA", "AA-", "A+", "A", "A-"}
    return rating if rating in valid else None


def fetch_candidates() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=300)
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    for col in ["maincode", "innercode", "basic_asset_type_code", "abs_level", "abs_level_y"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["remaining_years", "valuation_yield_pct"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(
        subset=[
            "maincode",
            "innercode",
            "bond_code",
            "bond_name",
            "remaining_years",
            "valuation_yield_pct",
        ]
    ).copy()
    if df.empty:
        return df
    df["maincode"] = df["maincode"].astype("int64")
    df["innercode"] = df["innercode"].astype("int64")
    return df.reset_index(drop=True)


def fetch_latest_ratings(maincodes: list[int]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for part in chunks(maincodes):
        sql = f"""
        SELECT maincode, rating_desc
        FROM (
            SELECT
                CAST(MAINCODE AS UNSIGNED) AS maincode,
                CRDESC AS rating_desc,
                ROW_NUMBER() OVER (
                    PARTITION BY MAINCODE
                    ORDER BY INFOPUBLDATE DESC, CRDATE DESC, ID DESC
                ) AS rn
            FROM bond_bdcreditgrading FORCE INDEX (IX_BOND_BDCREDITGRADING_Q047)
            WHERE MAINCODE IN ({sql_list(part)})
              AND INFOPUBLDATE <= DATE('{AS_OF_DATE}')
              AND (UPDATETIME IS NULL OR UPDATETIME < TIMESTAMP('{NEXT_DAY_TS}'))
              AND CRTYPECODE = 2
              AND CRSYSTEM = 1
              AND CRDESC IS NOT NULL
              AND (RATINGEFFECTIVEENDDATE IS NULL OR RATINGEFFECTIVEENDDATE >= DATE('{AS_OF_DATE}'))
        ) ranked
        WHERE rn = 1
        """
        frames.append(query_to_dataframe(sql, max_rows=0, timeout=300))

    ratings = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if ratings.empty:
        return pd.DataFrame(columns=["maincode", "rating_desc"])
    ratings["maincode"] = pd.to_numeric(ratings["maincode"], errors="coerce")
    ratings = ratings.dropna(subset=["maincode", "rating_desc"]).copy()
    ratings["maincode"] = ratings["maincode"].astype("int64")
    ratings["rating_desc"] = ratings["rating_desc"].map(normalize_rating)
    ratings = ratings.dropna(subset=["rating_desc"]).copy()
    return ratings.drop_duplicates("maincode", keep="first")


def fetch_curves(curve_codes: list[int]) -> pd.DataFrame:
    if not curve_codes:
        return pd.DataFrame()
    sql = f"""
    SELECT
        CAST(CURVECODE AS UNSIGNED) AS curve_code,
        CURVETYPE AS curve_name,
        YEARSTOMATURITY AS tenor,
        YIELD * 100 AS curve_yield_pct
    FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE ENDDATE = DATE('{AS_OF_DATE}')
      AND CURVECODE IN ({sql_list(curve_codes)})
      AND YIELDTYPECODE = 1
      AND YEARSTOMATURITY IS NOT NULL
      AND YIELD IS NOT NULL
    ORDER BY CURVECODE, YEARSTOMATURITY
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=300)
    if df is None or df.empty:
        return pd.DataFrame()
    for col in ["curve_code", "tenor", "curve_yield_pct"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["curve_code", "tenor", "curve_yield_pct"]).copy()
    df["curve_code"] = df["curve_code"].astype("int64")
    df["curve_name"] = df["curve_name"].astype(str)
    return df


def interpolate_curve_yields(sample: pd.DataFrame, curves: pd.DataFrame) -> pd.DataFrame:
    if sample.empty or curves.empty:
        return pd.DataFrame()

    curve_lookup: dict[int, tuple[str, np.ndarray, np.ndarray]] = {}
    for code, group in curves.groupby("curve_code", sort=False):
        group = group.drop_duplicates("tenor").sort_values("tenor")
        if len(group) < 2:
            continue
        curve_lookup[int(code)] = (
            str(group.iloc[0]["curve_name"]),
            group["tenor"].to_numpy(dtype=float),
            group["curve_yield_pct"].to_numpy(dtype=float),
        )

    rows: list[dict[str, object]] = []
    for _, row in sample.iterrows():
        code = int(row["curve_code_used"])
        curve = curve_lookup.get(code)
        if curve is None:
            continue
        curve_name, tenors, yields = curve
        remaining = float(row["remaining_years"])
        if remaining < float(tenors.min()) or remaining > float(tenors.max()):
            continue
        out = row.to_dict()
        out["curve_name_used"] = curve_name
        out["curve_yield_pct"] = float(np.interp(remaining, tenors, yields))
        out["deviation_bp"] = (float(row["valuation_yield_pct"]) - out["curve_yield_pct"]) * 100.0
        rows.append(out)
    return pd.DataFrame(rows)


def build_result() -> pd.DataFrame:
    candidates = fetch_candidates()
    if candidates.empty:
        return empty_result()

    ratings = fetch_latest_ratings(candidates["maincode"].tolist())
    if ratings.empty:
        return empty_result()
    sample = candidates.merge(ratings, on="maincode", how="inner")
    if sample.empty:
        return empty_result()

    sample["curve_code_used"] = sample["rating_desc"].map(CURVE_MAP)
    sample = sample.dropna(subset=["curve_code_used"]).copy()
    if sample.empty:
        return empty_result()
    sample["curve_code_used"] = sample["curve_code_used"].astype("int64")

    curves = fetch_curves(sample["curve_code_used"].unique().tolist())
    priced = interpolate_curve_yields(sample, curves)
    if priced.empty:
        return empty_result()

    result = priced.copy()
    result["relative_value_side"] = np.where(
        result["deviation_bp"] >= 0, "偏离最高", "偏离最低"
    )
    result["abs_deviation_bp"] = result["deviation_bp"].abs()
    result["side_order"] = result["relative_value_side"].map({"偏离最高": 0, "偏离最低": 1})
    result = result.sort_values(
        ["side_order", "abs_deviation_bp", "valuation_yield_pct", "bond_code"],
        ascending=[True, False, False, True],
    )
    out = pd.DataFrame(
        {
            "方向": result["relative_value_side"],
            "ABS代码": result["bond_code"],
            "简称": result["bond_name"],
            "评级": result["rating_desc"],
            "剩余期限": pd.to_numeric(result["remaining_years"], errors="coerce").round(4),
            "估值收益率": pd.to_numeric(result["valuation_yield_pct"], errors="coerce").round(4),
            "曲线收益率": pd.to_numeric(result["curve_yield_pct"], errors="coerce").round(4),
            "偏离值": pd.to_numeric(result["deviation_bp"], errors="coerce").round(2),
        }
    )
    return out[RESULT_COLUMNS].reset_index(drop=True)


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent)
    result = build_result()
    out_path = Path(__file__).with_name("result.csv")
    result.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"wrote {out_path} rows={len(result)}")
