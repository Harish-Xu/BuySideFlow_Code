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
from sweagent.text2sql.task_output_helper import finalize_run


AS_OF_DATE = "2024-12-31"
MATURITY_END_DATE = "2025-12-31"
MIN_LIVE_BOND_COUNT = 10
MIN_WALL_RATIO = 0.65
MIN_MATURITY_12M_SIZE_100M = 50.0
MAX_SHORT_MID_SPREAD_BP = 20.0
CHUNK_SIZE = 1200

CREDIT_L1 = (1600, 1700, 1800, 2500, 2600, 2800, 2900, 3100, 3200, 3300, 3700)
CREDIT_L2 = (1901, 1903)

RESULT_COLUMNS = [
    "issuer",
    "live_bond_count",
    "current_live_size_100m",
    "maturity_12m_size_100m",
    "maturity_12m_ratio",
    "median_yield_0_2y_pct",
    "median_yield_3_5y_pct",
    "short_minus_3_5y_spread_bp",
]


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def chunks(values, size=CHUNK_SIZE):
    values = sorted({int(v) for v in values if pd.notna(v)})
    for i in range(0, len(values), size):
        yield values[i : i + size]


def sql_list(values) -> str:
    return ",".join(str(int(v)) for v in values)


def first_valid(values: pd.Series) -> object:
    valid = values.dropna()
    if valid.empty:
        return np.nan
    return valid.iloc[0]


def latest_valuation_date() -> str | None:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        return None
    date_value = pd.to_datetime(df.loc[0, "valuation_date"], errors="coerce")
    if pd.isna(date_value):
        return None
    return date_value.strftime("%Y-%m-%d")


def fetch_live_credit_bonds() -> pd.DataFrame:
    sql = f"""
    SELECT
        CAST(bc.MAINCODE AS UNSIGNED) AS maincode,
        CAST(bc.INNERCODE AS UNSIGNED) AS innercode,
        CAST(bc.COMPANYCODE AS UNSIGNED) AS companycode,
        bc.SECUCODE AS bond_code,
        bc.SECUABBR AS bond_name,
        bc.ISSUER AS issuer,
        bc.SECUMARKET AS secumarket,
        DATE(bc.INTERESTENDDATE) AS maturity_date,
        bc.BONDTYPELEVEL1DESC AS bond_type_level1,
        bc.BONDTYPELEVEL2DESC AS bond_type_level2,
        bi.BONDFULLNAME AS bond_full_name,
        bi.OPTIONTYPE AS option_type,
        bi.OPMATURITY AS option_maturity
    FROM bond_code bc
    LEFT JOIN bond_basicinfo bi
      ON bc.MAINCODE = bi.MAINCODE
    WHERE bc.MAINCODE IS NOT NULL
      AND bc.INNERCODE IS NOT NULL
      AND bc.COMPANYCODE IS NOT NULL
      AND (bc.BONDTYPELEVEL1 IN ({sql_list(CREDIT_L1)}) OR bc.BONDTYPELEVEL2 IN ({sql_list(CREDIT_L2)}))
      AND COALESCE(bc.BONDTYPELEVEL1DESC, '') NOT LIKE '%资产支持%'
      AND COALESCE(bc.BONDTYPELEVEL2DESC, '') NOT LIKE '%资产支持%'
      AND COALESCE(bc.BONDTYPELEVEL1DESC, '') NOT IN ('可转债', '可交换公司债券', '分离交易可转债')
      AND COALESCE(bc.BONDTYPELEVEL2DESC, '') NOT IN ('可转债', '可交换公司债券', '分离交易可转债')
      AND COALESCE(bc.SECUABBR, '') NOT LIKE '%永续%'
      AND COALESCE(bc.CHINAME, '') NOT LIKE '%永续%'
      AND COALESCE(bi.BONDFULLNAME, '') NOT LIKE '%永续%'
      AND COALESCE(bi.OPTIONTYPE, '') NOT LIKE '%续期%'
      AND COALESCE(bi.OPTIONTYPE, '') NOT LIKE '%利息递延%'
      AND COALESCE(bi.OPMATURITY, '') NOT LIKE '%N%'
      AND (bc.LISTEDDATE IS NULL OR DATE(bc.LISTEDDATE) <= DATE('{AS_OF_DATE}'))
      AND (bc.INTERESTENDDATE IS NULL OR DATE(bc.INTERESTENDDATE) >= DATE('{AS_OF_DATE}'))
      AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) >= DATE('{AS_OF_DATE}'))
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    for col in ["maincode", "innercode", "companycode", "secumarket"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["maturity_date"] = pd.to_datetime(df["maturity_date"], errors="coerce")
    df = df.dropna(subset=["maincode", "innercode", "companycode"]).copy()
    if df.empty:
        return df

    for col in ["maincode", "innercode", "companycode"]:
        df[col] = df[col].astype("int64")
    return df.drop_duplicates(["maincode", "innercode"], keep="first").reset_index(drop=True)


def dedupe_maincode(live_codes: pd.DataFrame) -> pd.DataFrame:
    if live_codes.empty:
        return live_codes
    df = live_codes.copy()
    df["secumarket_rank"] = np.where(df["secumarket"] == 89, 0, 1)
    df = df.sort_values(
        ["maincode", "secumarket_rank", "bond_code", "innercode"],
        ascending=[True, True, True, True],
    )
    return df.drop_duplicates("maincode", keep="first").reset_index(drop=True)


def fetch_latest_sizes(maincodes: list[int]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for part in chunks(maincodes):
        sql = f"""
        SELECT
            CAST(MAINCODE AS UNSIGNED) AS maincode,
            DATE(ENDDATE) AS size_date,
            TOTALSIZE AS current_size_million
        FROM (
            SELECT
                s.MAINCODE,
                s.ENDDATE,
                s.TOTALSIZE,
                ROW_NUMBER() OVER (
                    PARTITION BY s.MAINCODE
                    ORDER BY s.ENDDATE DESC, s.INFOPUBLDATE DESC, s.ID DESC
                ) AS rn
            FROM bond_size s
            WHERE s.MAINCODE IN ({sql_list(part)})
              AND s.ENDDATE <= DATE('{AS_OF_DATE}')
              AND s.INFOPUBLDATE IS NOT NULL
              AND s.INFOPUBLDATE < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY)
        ) ranked
        WHERE rn = 1
          AND TOTALSIZE IS NOT NULL
          AND TOTALSIZE > 0
        """
        frames.append(query_to_dataframe(sql, max_rows=0, timeout=600))

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if df.empty:
        return df
    df = df.copy()
    df["maincode"] = pd.to_numeric(df["maincode"], errors="coerce")
    df["current_size_million"] = pd.to_numeric(df["current_size_million"], errors="coerce")
    df["size_date"] = pd.to_datetime(df["size_date"], errors="coerce")
    df = df.dropna(subset=["maincode", "current_size_million"]).copy()
    df["maincode"] = df["maincode"].astype("int64")
    return df.reset_index(drop=True)


def fetch_recommended_valuations(valuation_date: str) -> pd.DataFrame:
    sql = f"""
    SELECT
        CAST(INNERCODE AS UNSIGNED) AS innercode,
        DATE(ENDDATE) AS valuation_date,
        VPYIELD AS valuation_yield_pct,
        TRUEREMAINMATURITY AS remain_maturity_years,
        UPDATETIME,
        ID
    FROM bond_cbvaluationall FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
    WHERE ENDDATE >= DATE('{valuation_date}')
      AND ENDDATE < DATE_ADD(DATE('{valuation_date}'), INTERVAL 1 DAY)
      AND CREDIBILITYCODE = 1
      AND YIELDCODE = 2
      AND VPYIELD IS NOT NULL
      AND TRUEREMAINMATURITY IS NOT NULL
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    for col in ["innercode", "valuation_yield_pct", "remain_maturity_years", "ID"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["UPDATETIME"] = pd.to_datetime(df["UPDATETIME"], errors="coerce")
    df["valuation_date"] = pd.to_datetime(df["valuation_date"], errors="coerce")
    df = df.dropna(subset=["innercode", "valuation_yield_pct", "remain_maturity_years"]).copy()
    if df.empty:
        return df
    df["innercode"] = df["innercode"].astype("int64")
    df = df.sort_values(
        ["innercode", "UPDATETIME", "ID"],
        ascending=[True, False, False],
    )
    return df.drop_duplicates("innercode", keep="first").reset_index(drop=True)


def build_issuer_wall(live_main: pd.DataFrame, latest_size: pd.DataFrame) -> pd.DataFrame:
    if live_main.empty or latest_size.empty:
        return pd.DataFrame()

    df = live_main.merge(latest_size, on="maincode", how="inner")
    if df.empty:
        return df

    as_of = pd.Timestamp(AS_OF_DATE)
    maturity_end = pd.Timestamp(MATURITY_END_DATE)
    df["due_in_12m"] = (
        df["maturity_date"].notna()
        & (df["maturity_date"] > as_of)
        & (df["maturity_date"] <= maturity_end)
    )
    df["maturity_12m_size_million"] = np.where(
        df["due_in_12m"], df["current_size_million"], 0.0
    )

    issuer = (
        df.groupby("companycode", as_index=False)
        .agg(
            issuer=("issuer", first_valid),
            live_bond_count=("maincode", "nunique"),
            current_size_million=("current_size_million", "sum"),
            maturity_12m_size_million=("maturity_12m_size_million", "sum"),
        )
    )
    issuer["maturity_wall_ratio"] = (
        issuer["maturity_12m_size_million"] / issuer["current_size_million"]
    )
    issuer = issuer.replace([np.inf, -np.inf], np.nan).dropna(
        subset=["maturity_wall_ratio"]
    )
    issuer = issuer[
        (issuer["live_bond_count"] >= MIN_LIVE_BOND_COUNT)
        & (issuer["maturity_wall_ratio"] > MIN_WALL_RATIO)
        & (issuer["maturity_12m_size_million"] >= MIN_MATURITY_12M_SIZE_100M * 100.0)
    ].copy()
    issuer["companycode"] = issuer["companycode"].astype("int64")
    return issuer.reset_index(drop=True)


def build_term_yields(live_codes: pd.DataFrame, valuations: pd.DataFrame) -> pd.DataFrame:
    if live_codes.empty or valuations.empty:
        return pd.DataFrame()

    df = live_codes.merge(valuations, on="innercode", how="inner")
    if df.empty:
        return df
    df["secumarket_rank"] = np.where(df["secumarket"] == 89, 0, 1)
    df = df.sort_values(
        ["maincode", "secumarket_rank", "bond_code", "innercode", "UPDATETIME", "ID"],
        ascending=[True, True, True, True, False, False],
    )
    df = df.drop_duplicates("maincode", keep="first").copy()

    df["term_segment"] = pd.NA
    df.loc[
        (df["remain_maturity_years"] >= 0) & (df["remain_maturity_years"] <= 2),
        "term_segment",
    ] = "0-2Y"
    df.loc[
        (df["remain_maturity_years"] >= 3) & (df["remain_maturity_years"] <= 5),
        "term_segment",
    ] = "3-5Y"
    df = df.dropna(subset=["term_segment"]).copy()
    if df.empty:
        return pd.DataFrame()

    pivot = (
        df.pivot_table(
            index="companycode",
            columns="term_segment",
            values="valuation_yield_pct",
            aggfunc="median",
        )
        .reset_index()
        .rename(
            columns={
                "0-2Y": "median_yield_0_2y_pct",
                "3-5Y": "median_yield_3_5y_pct",
            }
        )
    )
    required = ["median_yield_0_2y_pct", "median_yield_3_5y_pct"]
    for col in required:
        if col not in pivot.columns:
            pivot[col] = np.nan
    pivot = pivot.dropna(subset=required).copy()
    if pivot.empty:
        return pivot
    pivot["companycode"] = pd.to_numeric(pivot["companycode"], errors="coerce").astype("int64")
    pivot["short_minus_3_5y_spread_bp"] = (
        pivot["median_yield_0_2y_pct"] - pivot["median_yield_3_5y_pct"]
    ) * 100.0
    return pivot.reset_index(drop=True)


def main() -> pd.DataFrame:
    live_codes = fetch_live_credit_bonds()
    if live_codes.empty:
        return empty_result()

    live_main = dedupe_maincode(live_codes)
    latest_size = fetch_latest_sizes(live_main["maincode"].tolist())
    issuer_wall = build_issuer_wall(live_main, latest_size)
    if issuer_wall.empty:
        return empty_result()

    valuation_date = latest_valuation_date()
    if valuation_date is None:
        return empty_result()

    valuations = fetch_recommended_valuations(valuation_date)
    term_yields = build_term_yields(live_codes, valuations)
    if term_yields.empty:
        return empty_result()

    result = issuer_wall.merge(term_yields, on="companycode", how="inner")
    result = result[result["short_minus_3_5y_spread_bp"] < MAX_SHORT_MID_SPREAD_BP].copy()
    if result.empty:
        return empty_result()

    result["current_live_size_100m"] = result["current_size_million"] / 100.0
    result["maturity_12m_size_100m"] = result["maturity_12m_size_million"] / 100.0
    result = result.rename(columns={"maturity_wall_ratio": "maturity_12m_ratio"})
    result = result.sort_values(
        ["maturity_12m_ratio", "live_bond_count", "issuer"],
        ascending=[False, False, True],
    )

    result["live_bond_count"] = result["live_bond_count"].astype(int)
    for col in [
        "current_live_size_100m",
        "maturity_12m_size_100m",
        "maturity_12m_ratio",
        "median_yield_0_2y_pct",
        "median_yield_3_5y_pct",
        "short_minus_3_5y_spread_bp",
    ]:
        result[col] = result[col].round(4)

    return result.reset_index(drop=True)[RESULT_COLUMNS]


if __name__ == "__main__":
    finalize_run(main())
