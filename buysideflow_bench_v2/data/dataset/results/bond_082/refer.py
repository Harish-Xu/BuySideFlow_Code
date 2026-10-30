#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = "2025-06-30"
NEXT_DATE = "2025-07-01"
CHUNK_SIZE = 3000
MIN_RATING_GAP_NOTCHES = 2
MIN_ANCHOR_COUNT = 20
TOP_N = 20

RATING_TO_RANK = {
    "AAA+": 0,
    "AAA": 1,
    "AAA-": 2,
    "AA+": 3,
    "AA": 4,
    "AA-": 5,
    "A+": 6,
    "A": 7,
    "A-": 8,
    "BBB+": 9,
    "BBB": 10,
    "BBB-": 11,
    "BB+": 12,
    "BB": 13,
    "BB-": 14,
    "B+": 15,
    "B": 16,
    "B-": 17,
    "CCC+": 18,
    "CCC": 19,
    "CCC-": 20,
    "CC": 21,
    "C": 22,
    "D": 23,
}
RANK_TO_RATING = {rank: rating for rating, rank in RATING_TO_RANK.items()}

RESULT_COLUMNS = [
    "债券代码",
    "债券简称",
    "发行人",
    "估值收益率(%)",
    "最高主体评级",
    "最低主体评级",
    "分歧档数",
    "隐含定价位置",
]


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def sql_list(values: list[int]) -> str:
    return ",".join(str(int(value)) for value in values)


def chunks(values: list[int], size: int = CHUNK_SIZE):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def normalize_rating(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip().upper()
    text = text.replace("（", "(").replace("）", ")")
    return text if text in RATING_TO_RANK else None


def load_valuation_sample() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df.empty:
        return df

    df = df.copy()
    numeric_cols = [
        "innercode",
        "maincode",
        "companycode",
        "valuation_yield_pct",
        "remain_maturity_years",
        "valuation_id",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["valuation_date"] = pd.to_datetime(df["valuation_date"], errors="coerce")
    df["valuation_insert_time"] = pd.to_datetime(df["valuation_insert_time"], errors="coerce")
    df["valuation_update_time"] = pd.to_datetime(df["valuation_update_time"], errors="coerce")
    df = df.dropna(
        subset=[
            "maincode",
            "companycode",
            "bond_code",
            "valuation_date",
            "valuation_yield_pct",
            "remain_maturity_years",
        ]
    ).copy()
    if df.empty:
        return df

    df["maincode"] = df["maincode"].astype("int64")
    df["companycode"] = df["companycode"].astype("int64")
    df = df[(df["remain_maturity_years"] > 3) & (df["remain_maturity_years"] <= 5)].copy()
    df = df.sort_values(
        ["maincode", "valuation_insert_time", "valuation_update_time", "valuation_id", "bond_code"],
        ascending=[True, False, False, False, True],
    )
    return df.drop_duplicates("maincode", keep="first").reset_index(drop=True)


def fetch_effective_ratings(companycodes: list[int]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for part in chunks(sorted(set(companycodes))):
        sql = f"""
        SELECT
            CAST(COMPANYCODE AS UNSIGNED) AS companycode,
            CAST(CRASCODE AS UNSIGNED) AS rating_agency_code,
            CRASNAME AS rating_agency,
            CRDESC AS rating_desc,
            DATE(CRDATE) AS rating_date,
            INFOPUBLDATE AS rating_publish_time,
            RATINGEFFECTIVEENDDATE AS rating_effective_end_time,
            INSERTTIME AS rating_insert_time,
            UPDATETIME AS rating_update_time,
            ID AS rating_id
        FROM bond_comcreditgrading FORCE INDEX (IX_BOND_COMCREDITGRADING)
        WHERE COMPANYCODE IN ({sql_list(part)})
          AND CRTYPE = 3
          AND CRSYSTEM = 1
          AND CRDESC IS NOT NULL
          AND CRDATE < DATE('{NEXT_DATE}')
          AND INFOPUBLDATE IS NOT NULL
          AND INFOPUBLDATE < TIMESTAMP('{NEXT_DATE}')
          AND (
                RATINGEFFECTIVEENDDATE IS NULL
             OR DATE(RATINGEFFECTIVEENDDATE) >= DATE('{AS_OF_DATE}')
          )
          AND CRSTATUS = 1
        """
        frames.append(query_to_dataframe(sql, max_rows=0, timeout=600))

    ratings = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if ratings.empty:
        return ratings

    ratings = ratings.copy()
    ratings["companycode"] = pd.to_numeric(ratings["companycode"], errors="coerce")
    ratings["rating_agency_code"] = pd.to_numeric(ratings["rating_agency_code"], errors="coerce")
    ratings["rating_id"] = pd.to_numeric(ratings["rating_id"], errors="coerce")
    ratings["rating_date"] = pd.to_datetime(ratings["rating_date"], errors="coerce")
    ratings["rating_publish_time"] = pd.to_datetime(ratings["rating_publish_time"], errors="coerce")
    ratings["rating_insert_time"] = pd.to_datetime(ratings["rating_insert_time"], errors="coerce")
    ratings["rating_update_time"] = pd.to_datetime(ratings["rating_update_time"], errors="coerce")
    ratings["rating_norm"] = ratings["rating_desc"].map(normalize_rating)
    ratings["rating_rank"] = ratings["rating_norm"].map(RATING_TO_RANK)
    ratings = ratings.dropna(
        subset=["companycode", "rating_agency_code", "rating_norm", "rating_rank", "rating_date"]
    ).copy()
    if ratings.empty:
        return ratings

    ratings["companycode"] = ratings["companycode"].astype("int64")
    ratings["rating_agency_code"] = ratings["rating_agency_code"].astype("int64")
    ratings["rating_rank"] = ratings["rating_rank"].astype(int)
    ratings = ratings.sort_values(
        [
            "companycode",
            "rating_agency_code",
            "rating_date",
            "rating_publish_time",
            "rating_insert_time",
            "rating_update_time",
            "rating_id",
        ],
        ascending=[True, True, False, False, False, False, False],
    )
    return ratings.drop_duplicates(["companycode", "rating_agency_code"], keep="first").reset_index(drop=True)


def build_anchor_stats(valuations: pd.DataFrame, ratings: pd.DataFrame) -> pd.DataFrame:
    issuer_ratings = (
        ratings.sort_values(["companycode", "rating_rank"], ascending=[True, False])
        .drop_duplicates("companycode", keep="first")
    )
    anchor_panel = valuations.merge(
        issuer_ratings[["companycode", "rating_norm"]],
        on="companycode",
        how="inner",
    )
    if anchor_panel.empty:
        return pd.DataFrame()

    anchor_panel = anchor_panel.drop_duplicates(["maincode", "rating_norm"]).copy()
    stats = (
        anchor_panel.groupby("rating_norm", as_index=False)
        .agg(
            anchor_yield=("valuation_yield_pct", "median"),
            anchor_count=("maincode", "nunique"),
        )
    )
    return stats[stats["anchor_count"] >= MIN_ANCHOR_COUNT].reset_index(drop=True)


def build_divergence_panel(valuations: pd.DataFrame, ratings: pd.DataFrame) -> pd.DataFrame:
    if valuations.empty or ratings.empty:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    rating_groups = ratings.groupby("companycode", sort=False)
    for _, bond in valuations.iterrows():
        maincode = int(bond["maincode"])
        companycode = int(bond["companycode"])
        if companycode not in rating_groups.groups:
            continue

        group = rating_groups.get_group(companycode).copy()
        agency_count = int(group["rating_agency_code"].nunique())
        if agency_count < 2:
            continue

        high_rank = int(group["rating_rank"].min())
        low_rank = int(group["rating_rank"].max())
        gap = low_rank - high_rank
        if gap < MIN_RATING_GAP_NOTCHES:
            continue

        rows.append(
            {
                "maincode": maincode,
                "companycode": companycode,
                "债券代码": str(bond["bond_code"]),
                "债券简称": bond["bond_name"],
                "发行人": bond["issuer"],
                "估值收益率(%)": float(bond["valuation_yield_pct"]),
                "最高主体评级": RANK_TO_RATING[high_rank],
                "最低主体评级": RANK_TO_RATING[low_rank],
                "分歧档数": int(gap),
            }
        )

    return pd.DataFrame(rows)


def add_implicit_pricing_position(divergence: pd.DataFrame, anchors: pd.DataFrame) -> pd.DataFrame:
    if divergence.empty or anchors.empty:
        return pd.DataFrame()

    high_anchor = anchors.rename(
        columns={
            "rating_norm": "最高评级",
            "anchor_yield": "high_anchor_yield",
            "anchor_count": "high_anchor_count",
        }
    )
    low_anchor = anchors.rename(
        columns={
            "rating_norm": "最低评级",
            "anchor_yield": "low_anchor_yield",
            "anchor_count": "low_anchor_count",
        }
    )

    result = divergence.merge(
        high_anchor[["最高评级", "high_anchor_yield", "high_anchor_count"]],
        left_on="最高主体评级",
        right_on="最高评级",
        how="inner",
    )
    result = result.merge(
        low_anchor[["最低评级", "low_anchor_yield", "low_anchor_count"]],
        left_on="最低主体评级",
        right_on="最低评级",
        how="inner",
    )
    if result.empty:
        return result

    denominator = result["low_anchor_yield"] - result["high_anchor_yield"]
    result = result[denominator > 0].copy()
    if result.empty:
        return result

    result["隐含定价位置"] = (
        (result["估值收益率(%)"] - result["high_anchor_yield"])
        / (result["low_anchor_yield"] - result["high_anchor_yield"])
    )
    return result


def main() -> pd.DataFrame:
    valuations = load_valuation_sample()
    if valuations.empty:
        return empty_result()

    ratings = fetch_effective_ratings(valuations["companycode"].dropna().astype("int64").tolist())
    if ratings.empty:
        return empty_result()

    anchors = build_anchor_stats(valuations, ratings)
    divergence = build_divergence_panel(valuations, ratings)
    result = add_implicit_pricing_position(divergence, anchors)
    if result.empty:
        return empty_result()

    result = result.sort_values(
        ["分歧档数", "隐含定价位置", "债券代码"],
        ascending=[False, False, True],
    ).head(TOP_N)

    result["估值收益率(%)"] = result["估值收益率(%)"].round(2)
    result["隐含定价位置"] = result["隐含定价位置"].round(2)
    result["分歧档数"] = result["分歧档数"].astype(int)
    return result.reset_index(drop=True)[RESULT_COLUMNS]


if __name__ == "__main__":
    output = main()
    output.to_csv(Path(__file__).with_name("result.csv"), index=False, encoding="utf-8-sig", float_format="%.2f")
