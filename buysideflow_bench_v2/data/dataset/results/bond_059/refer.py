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
MIN_REMAINING_YEARS = 1.0
MAX_REMAINING_YEARS = 5.0
TOP_TURNOVER_COUNT = 10
MIN_DEVIATION_BP = 2.0
CHUNK_SIZE = 500
QUERY_TIMEOUT = 600

CREDIT_LEVEL1 = (1600, 1700, 1800, 2500, 2600, 2800, 2900, 3100, 3200, 3300, 3700)
CREDIT_LEVEL2 = (1901, 1903)
TARGET_RATINGS = {"AAA", "AA+"}

OUTPUT_COLUMNS = ["债券代码", "债券简称", "成交额", "成交收益率", "估值收益率", "偏离值"]


def sql_num_list(values) -> str:
    return ",".join(str(int(v)) for v in values)


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


def fetch_valuation_rows() -> pd.DataFrame:
    sql = f"""
    WITH valuation_ranked AS (
        SELECT
            bc.MAINCODE,
            bc.INNERCODE,
            bc.SECUMARKET,
            bc.SECUCODE,
            bc.SECUABBR,
            bc.ISSUER,
            v.VPYIELD,
            v.TRUEYIELD,
            v.TRUEREMAINMATURITY,
            ROW_NUMBER() OVER (
                PARTITION BY bc.INNERCODE
                ORDER BY v.UPDATETIME DESC, v.INSERTTIME DESC, v.ID DESC
            ) AS rn
        FROM bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_Q047)
        JOIN bond_code bc
          ON v.INNERCODE = bc.INNERCODE
        WHERE v.ENDDATE >= DATE('{AS_OF_DATE}')
          AND v.ENDDATE < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY)
          AND v.CREDIBILITYCODE = 1
          AND v.YIELDCODE = 2
          AND v.VPYIELD IS NOT NULL
          AND v.TRUEYIELD IS NOT NULL
          AND (v.INSERTTIME IS NULL OR v.INSERTTIME < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY))
          AND v.UPDATETIME < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY)
          AND v.TRUEREMAINMATURITY > {MIN_REMAINING_YEARS}
          AND v.TRUEREMAINMATURITY <= {MAX_REMAINING_YEARS}
          AND bc.MAINCODE IS NOT NULL
          AND bc.INNERCODE IS NOT NULL
          AND bc.SECUMARKET IS NOT NULL
          AND bc.SECUCODE IS NOT NULL
          AND bc.SECUABBR IS NOT NULL
          AND (
                bc.BONDTYPELEVEL1 IN ({sql_num_list(CREDIT_LEVEL1)})
             OR bc.BONDTYPELEVEL2 IN ({sql_num_list(CREDIT_LEVEL2)})
          )
          AND (bc.LISTEDDATE IS NULL OR DATE(bc.LISTEDDATE) <= DATE('{AS_OF_DATE}'))
          AND (bc.INTERESTENDDATE IS NULL OR DATE(bc.INTERESTENDDATE) > DATE('{AS_OF_DATE}'))
          AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) > DATE('{AS_OF_DATE}'))
    )
    SELECT
        MAINCODE,
        INNERCODE,
        SECUMARKET,
        SECUCODE,
        SECUABBR,
        ISSUER,
        VPYIELD,
        TRUEYIELD,
        TRUEREMAINMATURITY
    FROM valuation_ranked
    WHERE rn = 1;
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=QUERY_TIMEOUT)
    if df.empty:
        return df
    for col in ["MAINCODE", "INNERCODE", "SECUMARKET"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    for col in ["VPYIELD", "TRUEYIELD", "TRUEREMAINMATURITY"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["MAINCODE", "INNERCODE", "SECUMARKET", "VPYIELD", "TRUEYIELD"])


def fetch_latest_ratings(maincodes: pd.Series | list[int]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for part in chunks(sorted(set(int(v) for v in maincodes if pd.notna(v)))):
        sql = f"""
        WITH ranked AS (
            SELECT
                MAINCODE,
                CRDESC,
                INFOPUBLDATE,
                CRDATE,
                ID,
                ROW_NUMBER() OVER (
                    PARTITION BY MAINCODE
                    ORDER BY INFOPUBLDATE DESC, CRDATE DESC, UPDATETIME DESC, ID DESC
                ) AS rn
            FROM bond_bdcreditgrading
            WHERE MAINCODE IN ({sql_num_list(part)})
              AND INFOPUBLDATE < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY)
              AND CRTYPECODE = 2
              AND CRSYSTEM = 1
              AND CRSTATUS = 1
              AND CRDESC IS NOT NULL
              AND (INSERTTIME IS NULL OR INSERTTIME < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY))
              AND UPDATETIME < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY)
              AND (
                  RATINGEFFECTIVEENDDATE IS NULL
                  OR RATINGEFFECTIVEENDDATE >= DATE('{AS_OF_DATE}')
              )
        )
        SELECT MAINCODE, CRDESC
        FROM ranked
        WHERE rn = 1;
        """
        frame = query_to_dataframe(sql, max_rows=0, timeout=QUERY_TIMEOUT)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["MAINCODE", "CRDESC"])
    ratings = pd.concat(frames, ignore_index=True)
    ratings["MAINCODE"] = pd.to_numeric(ratings["MAINCODE"], errors="coerce").astype("Int64")
    return ratings.dropna(subset=["MAINCODE"])


def fetch_turnover(valuation_rows: pd.DataFrame) -> pd.DataFrame:
    if valuation_rows.empty:
        return pd.DataFrame(columns=["MAINCODE", "INNERCODE", "SECUMARKET", "turnover_yuan"])

    pairs = valuation_rows[["INNERCODE", "SECUMARKET"]].drop_duplicates().copy()
    frames: list[pd.DataFrame] = []
    for start in range(0, len(pairs), CHUNK_SIZE):
        batch = pairs.iloc[start : start + CHUNK_SIZE]
        sql = f"""
        SELECT
            q.INNERCODE,
            q.SECUMARKET,
            SUM(q.TURNOVERVALUE) AS turnover_yuan
        FROM bond_basicquote q FORCE INDEX (IX_BOND_BASICQUOTE)
        WHERE (q.INNERCODE, q.SECUMARKET) IN ({pair_sql_list(batch)})
          AND q.TRADINGDAY >= DATE('{AS_OF_DATE}')
          AND q.TRADINGDAY < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY)
          AND q.TURNOVERVALUE IS NOT NULL
          AND q.TURNOVERVALUE > 0
          AND q.INSERTTIME < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY)
        GROUP BY q.INNERCODE, q.SECUMARKET;
        """
        frame = query_to_dataframe(sql, max_rows=0, timeout=QUERY_TIMEOUT)
        if not frame.empty:
            frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=["INNERCODE", "SECUMARKET", "turnover_yuan"])
    turnover = pd.concat(frames, ignore_index=True)
    turnover["INNERCODE"] = pd.to_numeric(turnover["INNERCODE"], errors="coerce").astype("Int64")
    turnover["SECUMARKET"] = pd.to_numeric(turnover["SECUMARKET"], errors="coerce").astype("Int64")
    turnover["turnover_yuan"] = pd.to_numeric(turnover["turnover_yuan"], errors="coerce")
    return turnover.dropna(subset=["INNERCODE", "SECUMARKET", "turnover_yuan"])


def build_result(valuation_rows: pd.DataFrame, ratings: pd.DataFrame, turnover: pd.DataFrame) -> pd.DataFrame:
    if valuation_rows.empty or ratings.empty or turnover.empty:
        return empty_result()

    rated = valuation_rows.merge(ratings, on="MAINCODE", how="inner")
    rated = rated[rated["CRDESC"].isin(TARGET_RATINGS)].copy()
    if rated.empty:
        return empty_result()

    traded = rated.merge(turnover, on=["INNERCODE", "SECUMARKET"], how="inner")
    traded = traded[traded["turnover_yuan"] > 0].copy()
    if traded.empty:
        return empty_result()

    traded["weighted_true_yield"] = traded["TRUEYIELD"] * traded["turnover_yuan"]
    traded["weighted_vp_yield"] = traded["VPYIELD"] * traded["turnover_yuan"]

    grouped = (
        traded.groupby("MAINCODE", as_index=False)
        .agg(
            turnover_yuan=("turnover_yuan", "sum"),
            weighted_true_yield=("weighted_true_yield", "sum"),
            weighted_vp_yield=("weighted_vp_yield", "sum"),
        )
    )
    grouped["trade_yield_pct"] = grouped["weighted_true_yield"] / grouped["turnover_yuan"]
    grouped["valuation_yield_pct"] = grouped["weighted_vp_yield"] / grouped["turnover_yuan"]

    representative = (
        traded.sort_values(["MAINCODE", "turnover_yuan", "SECUCODE"], ascending=[True, False, True])
        .drop_duplicates("MAINCODE", keep="first")[["MAINCODE", "SECUCODE", "SECUABBR"]]
    )
    panel = grouped.merge(representative, on="MAINCODE", how="left")
    panel = panel.sort_values(["turnover_yuan", "SECUCODE"], ascending=[False, True]).reset_index(drop=True)
    panel = panel.head(TOP_TURNOVER_COUNT).copy()

    panel["deviation_bp"] = (panel["trade_yield_pct"] - panel["valuation_yield_pct"]) * 100.0
    panel = panel[panel["deviation_bp"] > MIN_DEVIATION_BP].copy()
    if panel.empty:
        return empty_result()

    result = pd.DataFrame(
        {
            "债券代码": panel["SECUCODE"].astype(str),
            "债券简称": panel["SECUABBR"],
            "成交额": panel["turnover_yuan"].round(2),
            "成交收益率": panel["trade_yield_pct"].round(4),
            "估值收益率": panel["valuation_yield_pct"].round(4),
            "偏离值": panel["deviation_bp"].round(2),
        }
    )
    return result.sort_values(
        ["偏离值", "成交额", "债券代码"],
        ascending=[False, False, True],
    ).reset_index(drop=True)


def main() -> pd.DataFrame:
    with shared_connection_session():
        valuation_rows = fetch_valuation_rows()
        if valuation_rows.empty:
            return write_result(empty_result())
        ratings = fetch_latest_ratings(valuation_rows["MAINCODE"])
        if ratings.empty:
            return write_result(empty_result())
        turnover = fetch_turnover(valuation_rows)
        result = build_result(valuation_rows, ratings, turnover)
        return write_result(result)


if __name__ == "__main__":
    main()
