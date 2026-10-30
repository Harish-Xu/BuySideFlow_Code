#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe, shared_connection_session


AS_OF_DATE = "2024-12-31"
AS_OF_EXCLUSIVE = "2025-01-01"
MIN_REMAINING_YEARS = 3.0
MAX_REMAINING_YEARS = 5.0
MIN_TRADE_DAYS = 1
TOP_N = 5
CHUNK_SIZE = 500
QUERY_TIMEOUT = 600

MTN_TYPE_LEVEL1 = 1800
MTN_TYPE_LEVEL2 = 1800
TARGET_RATINGS = {"AAA"}
TARGET_INDUSTRY = "交通运输"

OUTPUT_COLUMNS = [
    "债券代码",
    "债券简称",
    "发行人",
    "估值收益率",
    "成交天数",
    "成交金额",
]


def sql_num_list(values) -> str:
    return ",".join(str(int(v)) for v in values)


def sql_str_list(values) -> str:
    return ",".join(f"'{str(v)}'" for v in values)


def pair_sql_list(frame: pd.DataFrame) -> str:
    return ",".join(f"({int(row.INNERCODE)},{int(row.SECUMARKET)})" for row in frame.itertuples())


def chunks(values, size: int = CHUNK_SIZE):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start : start + size]


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def write_result(result: pd.DataFrame) -> pd.DataFrame:
    result = result.loc[:, OUTPUT_COLUMNS].copy()
    out_path = Path(__file__).with_name("result.csv")
    result.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


def fetch_valuation_date() -> str:
    sql = f"""
    SELECT DATE(ENDDATE) AS valuation_date
    FROM bond_cbvaluationall FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
    WHERE ENDDATE < DATE('{AS_OF_EXCLUSIVE}')
      AND CREDIBILITYCODE = 1
      AND VPYIELD IS NOT NULL
      AND TRUEREMAINMATURITY IS NOT NULL
      AND (INSERTTIME IS NULL OR INSERTTIME < DATE('{AS_OF_EXCLUSIVE}'))
    ORDER BY ENDDATE DESC
    LIMIT 1;
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=QUERY_TIMEOUT)
    if df.empty:
        raise ValueError("No ChinaBond valuation date found on or before as_of_date")
    return pd.Timestamp(df.iloc[0]["valuation_date"]).date().isoformat()


def fetch_recent_trade_days(as_of_date: str) -> list[str]:
    sql = f"""
    SELECT DATE(TRADINGDATE) AS trade_date
    FROM qt_tradingdaynew
    WHERE IFTRADINGDAY = 1
      AND SECUMARKET = 89
      AND TRADINGDATE <= DATE('{as_of_date}')
    ORDER BY TRADINGDATE DESC
    LIMIT 20;
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=QUERY_TIMEOUT)
    if len(df) < 20:
        raise ValueError("Fewer than 20 bond trading days are available")
    return sorted(pd.to_datetime(df["trade_date"]).dt.date.astype(str).tolist())


def fetch_candidate_pool(valuation_date: str) -> pd.DataFrame:
    sql = f"""
    WITH valuation_ranked AS (
        SELECT
            bc.MAINCODE,
            bc.INNERCODE,
            bc.SECUMARKET,
            bc.SECUCODE,
            bc.SECUABBR,
            bc.ISSUER,
            bc.COMPANYCODE,
            v.VPYIELD,
            v.TRUEREMAINMATURITY,
            ROW_NUMBER() OVER (
                PARTITION BY bc.MAINCODE
                ORDER BY
                    CASE WHEN bc.SECUMARKET = 89 THEN 0 ELSE 1 END,
                    COALESCE(bc.SECUCODE, ''),
                    bc.INNERCODE,
                    v.ID DESC
            ) AS rn
        FROM bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_Q047)
        JOIN bond_code bc
          ON v.INNERCODE = bc.INNERCODE
        JOIN bond_basicinfo bi
          ON bc.MAINCODE = bi.MAINCODE
        WHERE v.ENDDATE >= DATE('{valuation_date}')
          AND v.ENDDATE < DATE_ADD(DATE('{valuation_date}'), INTERVAL 1 DAY)
          AND v.CREDIBILITYCODE = 1
          AND v.YIELDCODE = 2
          AND v.VPYIELD IS NOT NULL
          AND v.TRUEREMAINMATURITY > {MIN_REMAINING_YEARS}
          AND v.TRUEREMAINMATURITY <= {MAX_REMAINING_YEARS}
          AND (v.INSERTTIME IS NULL OR v.INSERTTIME < DATE('{AS_OF_EXCLUSIVE}'))
          AND (bc.BONDTYPELEVEL1 = {MTN_TYPE_LEVEL1} OR bc.BONDTYPELEVEL2 = {MTN_TYPE_LEVEL2})
          AND bc.MAINCODE IS NOT NULL
          AND bc.SECUCODE IS NOT NULL
          AND bc.SECUABBR IS NOT NULL
          AND bc.ISSUER IS NOT NULL
          AND (bc.LISTEDDATE IS NULL OR DATE(bc.LISTEDDATE) <= DATE('{AS_OF_DATE}'))
          AND DATE(bc.INTERESTENDDATE) > DATE('{AS_OF_DATE}')
          AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) > DATE('{AS_OF_DATE}'))
          AND COALESCE(bc.SECUABBR, '') NOT LIKE '%永续%'
          AND COALESCE(bc.CHINAME, '') NOT LIKE '%永续%'
          AND COALESCE(bi.BONDFULLNAME, '') NOT LIKE '%永续%'
          AND COALESCE(bc.SECUABBR, '') NOT LIKE '%次级%'
          AND COALESCE(bc.CHINAME, '') NOT LIKE '%次级%'
          AND COALESCE(bi.BONDFULLNAME, '') NOT LIKE '%次级%'
          AND COALESCE(bc.BONDTYPELEVEL1DESC, '') NOT LIKE '%次级%'
          AND COALESCE(bc.BONDTYPELEVEL2DESC, '') NOT LIKE '%次级%'
          AND COALESCE(bi.BONDNATURE, -1) NOT IN (3, 21)
          AND NOT EXISTS (
              SELECT 1
              FROM bond_conceptnature cn
              JOIN bond_code cb ON cb.INNERCODE = cn.INNERCODE
              WHERE cb.MAINCODE = bc.MAINCODE
                AND cn.CONCEPTTYPE = 40
                AND cn.CONNATURE = 4005
                AND (cn.BEGINDATE IS NULL OR cn.BEGINDATE <= DATE('{AS_OF_DATE}'))
                AND (cn.ENDDATE IS NULL OR cn.ENDDATE > DATE('{AS_OF_DATE}'))
                AND cn.INSERTTIME < DATE('{AS_OF_EXCLUSIVE}')
          )
          AND NOT EXISTS (
              SELECT 1
              FROM bond_default def
              JOIN bond_code dc
                ON def.INNERCODE = dc.INNERCODE
              WHERE dc.MAINCODE = bc.MAINCODE
                AND def.INFOPUBLDATE < DATE('{AS_OF_EXCLUSIVE}')
                AND (def.DEFAULTDATE IS NULL OR def.DEFAULTDATE < DATE('{AS_OF_EXCLUSIVE}'))
                -- Actual interest/principal/other default; exclude forecasts and warnings.
                AND def.DEFAULTPROG IN (201, 202, 203, 801)
          )
    )
    SELECT
        MAINCODE,
        INNERCODE,
        SECUMARKET,
        SECUCODE,
        SECUABBR,
        ISSUER,
        COMPANYCODE,
        VPYIELD,
        TRUEREMAINMATURITY
    FROM valuation_ranked
    WHERE rn = 1;
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=QUERY_TIMEOUT)
    if df.empty:
        return df
    df["MAINCODE"] = pd.to_numeric(df["MAINCODE"]).astype("int64")
    df["INNERCODE"] = pd.to_numeric(df["INNERCODE"]).astype("int64")
    df["SECUMARKET"] = pd.to_numeric(df["SECUMARKET"]).astype("int64")
    df["COMPANYCODE"] = pd.to_numeric(df["COMPANYCODE"], errors="coerce").astype("Int64")
    df["VPYIELD"] = pd.to_numeric(df["VPYIELD"], errors="coerce")
    df["TRUEREMAINMATURITY"] = pd.to_numeric(df["TRUEREMAINMATURITY"], errors="coerce")
    return df


def fetch_latest_ratings(maincodes: pd.Series | list[int], as_of_date: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for part in chunks(sorted(set(int(v) for v in maincodes))):
        sql = f"""
        WITH ranked AS (
            SELECT
                MAINCODE,
                CRDESC,
                INFOPUBLDATE,
                CRDATE,
                CRSTATUS,
                RATINGEFFECTIVEENDDATE,
                ID,
                ROW_NUMBER() OVER (
                    PARTITION BY MAINCODE
                    ORDER BY INFOPUBLDATE DESC, CRDATE DESC, ID DESC
                ) AS rn
            FROM bond_bdcreditgrading
            WHERE MAINCODE IN ({sql_num_list(part)})
              AND INFOPUBLDATE < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
              AND CRTYPECODE = 2
              AND CRSYSTEM = 1
        )
        SELECT MAINCODE, CRDESC
        FROM ranked
        WHERE rn = 1
          AND CRSTATUS = 1
          AND CRDESC IS NOT NULL
          AND (RATINGEFFECTIVEENDDATE IS NULL OR RATINGEFFECTIVEENDDATE >= DATE('{as_of_date}'));
        """
        frame = query_to_dataframe(sql, max_rows=0, timeout=QUERY_TIMEOUT)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["MAINCODE", "CRDESC"])
    ratings = pd.concat(frames, ignore_index=True)
    ratings["MAINCODE"] = pd.to_numeric(ratings["MAINCODE"]).astype("int64")
    return ratings


def fetch_latest_industries(companycodes: pd.Series | list[int], as_of_date: str) -> pd.DataFrame:
    codes = sorted(set(int(v) for v in companycodes if pd.notna(v)))
    frames: list[pd.DataFrame] = []
    for part in chunks(codes):
        sql = f"""
        WITH ranked AS (
            SELECT
                COMPANYCODE,
                FIRSTINDUSTRYNAME,
                EFFECTIVEDATE,
                INFOPUBLDATE,
                ID,
                ROW_NUMBER() OVER (
                    PARTITION BY COMPANYCODE
                    ORDER BY EFFECTIVEDATE DESC, INFOPUBLDATE DESC, ID DESC
                ) AS rn
            FROM bond_biindustry
            WHERE COMPANYCODE IN ({sql_num_list(part)})
              AND STANDARD = 38
              AND EFFECTIVEDATE <= DATE('{as_of_date}')
              AND INFOPUBLDATE < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
              AND (CANCELDATE IS NULL OR CANCELDATE > DATE('{as_of_date}'))
        )
        SELECT COMPANYCODE, FIRSTINDUSTRYNAME
        FROM ranked
        WHERE rn = 1;
        """
        frame = query_to_dataframe(sql, max_rows=0, timeout=QUERY_TIMEOUT)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["COMPANYCODE", "FIRSTINDUSTRYNAME"])
    industries = pd.concat(frames, ignore_index=True)
    industries["COMPANYCODE"] = pd.to_numeric(industries["COMPANYCODE"]).astype("Int64")
    return industries


def fetch_listing_pairs(maincodes: pd.Series | list[int], as_of_date: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for part in chunks(sorted(set(int(v) for v in maincodes))):
        sql = f"""
        SELECT DISTINCT
            MAINCODE,
            INNERCODE,
            SECUMARKET
        FROM bond_code
        WHERE MAINCODE IN ({sql_num_list(part)})
          AND INNERCODE IS NOT NULL
          AND SECUMARKET IS NOT NULL
          AND (LISTEDDATE IS NULL OR DATE(LISTEDDATE) <= DATE('{as_of_date}'))
          AND DATE(INTERESTENDDATE) > DATE('{as_of_date}')
          AND (DELISTDATE IS NULL OR DATE(DELISTDATE) > DATE('{as_of_date}'));
        """
        frame = query_to_dataframe(sql, max_rows=0, timeout=QUERY_TIMEOUT)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["MAINCODE", "INNERCODE", "SECUMARKET"])
    listings = pd.concat(frames, ignore_index=True).drop_duplicates()
    for col in ["MAINCODE", "INNERCODE", "SECUMARKET"]:
        listings[col] = pd.to_numeric(listings[col]).astype("int64")
    return listings


def fetch_trade_activity(listings: pd.DataFrame, trade_days: list[str]) -> pd.DataFrame:
    if listings.empty or not trade_days:
        return pd.DataFrame(columns=["MAINCODE", "trade_days_20", "turnover_yuan"])

    date_sql = sql_str_list(trade_days)
    frames: list[pd.DataFrame] = []
    for start in range(0, len(listings), CHUNK_SIZE):
        batch = listings.iloc[start : start + CHUNK_SIZE]
        sql = f"""
        SELECT
            bc.MAINCODE,
            DATE(q.TRADINGDAY) AS trade_date,
            SUM(CASE WHEN COALESCE(q.TURNOVERVALUE, 0) > 0 THEN q.TURNOVERVALUE ELSE 0 END) AS turnover_yuan
        FROM bond_basicquote q FORCE INDEX (IX_BOND_BASICQUOTE)
        JOIN bond_code bc
          ON q.INNERCODE = bc.INNERCODE
         AND q.SECUMARKET = bc.SECUMARKET
        WHERE (q.INNERCODE, q.SECUMARKET) IN ({pair_sql_list(batch)})
          AND q.TRADINGDAY IN ({date_sql})
          AND q.INSERTTIME < DATE('{AS_OF_EXCLUSIVE}')
          AND (
              COALESCE(q.TURNOVERVALUE, 0) > 0
              OR COALESCE(q.TURNOVERVOLUME, 0) > 0
          )
        GROUP BY bc.MAINCODE, DATE(q.TRADINGDAY);
        """
        frame = query_to_dataframe(sql, max_rows=0, timeout=QUERY_TIMEOUT)
        if not frame.empty:
            frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=["MAINCODE", "trade_days_20", "turnover_yuan"])

    trades = pd.concat(frames, ignore_index=True)
    trades["MAINCODE"] = pd.to_numeric(trades["MAINCODE"]).astype("int64")
    trades["trade_date"] = pd.to_datetime(trades["trade_date"])
    trades["turnover_yuan"] = pd.to_numeric(trades["turnover_yuan"], errors="coerce").fillna(0.0)
    grouped = (
        trades.groupby("MAINCODE", as_index=False)
        .agg(
            trade_days_20=("trade_date", "nunique"),
            turnover_yuan=("turnover_yuan", "sum"),
        )
    )
    return grouped


def build_result(
    candidates: pd.DataFrame,
    ratings: pd.DataFrame,
    industries: pd.DataFrame,
    trades: pd.DataFrame,
) -> pd.DataFrame:
    if candidates.empty or ratings.empty or trades.empty:
        return empty_result()

    panel = candidates.merge(ratings, on="MAINCODE", how="inner")
    panel = panel[panel["CRDESC"].isin(TARGET_RATINGS)].copy()
    if panel.empty:
        return empty_result()

    panel = panel.merge(industries, on="COMPANYCODE", how="left")
    panel = panel[panel["FIRSTINDUSTRYNAME"].eq(TARGET_INDUSTRY)].copy()
    panel = panel.merge(trades, on="MAINCODE", how="inner")
    panel["trade_days_20"] = pd.to_numeric(panel["trade_days_20"], errors="coerce").fillna(0).astype("int64")
    panel["turnover_yuan"] = pd.to_numeric(panel["turnover_yuan"], errors="coerce").fillna(0.0)
    panel = panel[panel["trade_days_20"] >= MIN_TRADE_DAYS].copy()
    if panel.empty:
        return empty_result()

    panel = panel.sort_values(
        ["trade_days_20", "turnover_yuan", "SECUCODE"],
        ascending=[False, False, True],
    )
    selected = panel.head(TOP_N).copy()

    result = pd.DataFrame(
        {
            "债券代码": selected["SECUCODE"].astype(str),
            "债券简称": selected["SECUABBR"],
            "发行人": selected["ISSUER"],
            "估值收益率": selected["VPYIELD"].round(2),
            "成交天数": selected["trade_days_20"].astype(int),
            "成交金额": (selected["turnover_yuan"] / 100_000_000).round(2),
        }
    )
    return result.reset_index(drop=True)


def main() -> pd.DataFrame:
    with shared_connection_session():
        valuation_date = fetch_valuation_date()
        trade_days = fetch_recent_trade_days(AS_OF_DATE)
        candidates = fetch_candidate_pool(valuation_date)
        if candidates.empty:
            return write_result(empty_result())
        ratings = fetch_latest_ratings(candidates["MAINCODE"], AS_OF_DATE)
        if ratings.empty:
            return write_result(empty_result())
        industries = fetch_latest_industries(candidates["COMPANYCODE"], AS_OF_DATE)
        rated = candidates.merge(ratings, on="MAINCODE", how="inner")
        rated = rated[rated["CRDESC"].isin(TARGET_RATINGS)].copy()
        rated = rated.merge(industries, on="COMPANYCODE", how="inner")
        rated = rated[rated["FIRSTINDUSTRYNAME"].eq(TARGET_INDUSTRY)].copy()
        if rated.empty:
            return write_result(empty_result())
        listings = fetch_listing_pairs(rated["MAINCODE"], AS_OF_DATE)
        trades = fetch_trade_activity(listings, trade_days)
        result = build_result(candidates, ratings, industries, trades)
        return write_result(result)


if __name__ == "__main__":
    main()
