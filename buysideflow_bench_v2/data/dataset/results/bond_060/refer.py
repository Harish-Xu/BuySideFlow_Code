#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Iterable

import pandas as pd
import pymysql
from pymysql.cursors import DictCursor


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import (  # noqa: E402
    _convert_value,
    _normalize_sql_for_mysql,
    _patch_sql_aliases,
)


AS_OF_DATE = "2025-01-03"
Q4_START = "2024-10-01"
Q4_END_EXCLUSIVE = "2025-01-01"
TOP_N = 10
MTN_TYPE_CODE = 1800
CURVE_CODE_BY_RATING = {"AA+": 68, "AAA": 69}
TARGET_RATINGS = set(CURVE_CODE_BY_RATING)
CHUNK_SIZE = 500
QUERY_TIMEOUT = 120

OUTPUT_COLUMNS = [
    "新券代码",
    "简称",
    "发行人",
    "发行规模",
    "债项评级",
    "发行期限",
    "发行利率",
    "曲线基准收益率",
    "发行利差",
]

_CONNECTION = None


def sql_num_list(values: Iterable[int]) -> str:
    return ",".join(str(int(v)) for v in values)


def chunks(values: Iterable[int], size: int = CHUNK_SIZE):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start : start + size]


def query_to_dataframe(sql: str, max_rows: int = 0, timeout: int | None = None) -> pd.DataFrame:
    global _CONNECTION
    normalized_sql = _patch_sql_aliases(_normalize_sql_for_mysql(sql))
    socket_timeout = int(timeout or QUERY_TIMEOUT)

    for query_attempt in range(2):
        if _CONNECTION is None or not getattr(_CONNECTION, "open", False):
            for connect_attempt in range(3):
                try:
                    _CONNECTION = pymysql.connect(
                        host=os.getenv("DB_HOST", "localhost"),
                        port=int(os.getenv("DB_PORT", "3306")),
                        user=os.getenv("DB_USER", "root"),
                        password=os.getenv("DB_PASSWORD", ""),
                        database=os.getenv("DB_NAME", "jydb"),
                        charset="utf8mb4",
                        cursorclass=DictCursor,
                        autocommit=True,
                        connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT_SEC", "5")),
                        read_timeout=socket_timeout,
                        write_timeout=socket_timeout,
                    )
                    break
                except Exception:
                    if connect_attempt == 2:
                        raise
                    time.sleep(1.5 * (connect_attempt + 1))
        try:
            with _CONNECTION.cursor() as cursor:
                cursor.execute(normalized_sql)
                rows = cursor.fetchall() if max_rows == 0 else cursor.fetchmany(max_rows)
            return pd.DataFrame([{key: _convert_value(value) for key, value in row.items()} for row in rows])
        except pymysql.MySQLError:
            try:
                _CONNECTION.close()
            except Exception:
                pass
            _CONNECTION = None
            if query_attempt == 1:
                raise
            time.sleep(1.5)

    return pd.DataFrame()


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def write_result(result: pd.DataFrame) -> pd.DataFrame:
    result = result.loc[:, OUTPUT_COLUMNS].copy()
    out_path = Path(__file__).with_name("result.csv")
    result.to_csv(out_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_csv(index=False, float_format="%.2f").strip())
    return result


def new_issue_term_filter(bi_alias: str = "bi", bc_alias: str = "co") -> str:
    return f"""
      AND {bi_alias}.RATEIFFLOAT = 2
      AND {bi_alias}.COMPOUNDMETHOD = 1
      AND COALESCE({bc_alias}.SECUABBR, '') NOT LIKE '%永续%'
      AND COALESCE({bc_alias}.CHINAME, '') NOT LIKE '%永续%'
      AND COALESCE({bi_alias}.BONDFULLNAME, '') NOT LIKE '%永续%'
      AND COALESCE({bi_alias}.OPMATURITY, '') NOT LIKE '%N%'
      AND COALESCE({bi_alias}.OPTIONTYPE, '') NOT LIKE '%续期%'
      AND COALESCE({bi_alias}.OPTIONTYPE, '') NOT LIKE '%利息递延%'
      AND COALESCE({bc_alias}.SECUABBR, '') NOT LIKE '%次级%'
      AND COALESCE({bc_alias}.CHINAME, '') NOT LIKE '%次级%'
      AND COALESCE({bi_alias}.BONDFULLNAME, '') NOT LIKE '%次级%'
      AND COALESCE({bc_alias}.BONDTYPELEVEL1DESC, '') NOT LIKE '%次级%'
      AND COALESCE({bc_alias}.BONDTYPELEVEL2DESC, '') NOT LIKE '%次级%'
    """


def fetch_new_issue_candidates() -> pd.DataFrame:
    sql = f"""
    WITH code_ranked AS (
        SELECT
            bc.MAINCODE,
            bc.INNERCODE,
            bc.SECUMARKET,
            bc.SECUCODE,
            bc.SECUABBR,
            bc.CHINAME,
            bc.ISSUER,
            bc.BONDTYPELEVEL1,
            bc.BONDTYPELEVEL1DESC,
            bc.BONDTYPELEVEL2,
            bc.BONDTYPELEVEL2DESC,
            ROW_NUMBER() OVER (
                PARTITION BY bc.MAINCODE
                ORDER BY
                    CASE WHEN bc.SECUMARKET = 89 THEN 0 ELSE 1 END,
                    COALESCE(bc.SECUCODE, ''),
                    bc.INNERCODE
            ) AS rn
        FROM bond_code bc
        WHERE bc.MAINCODE IS NOT NULL
          AND bc.SECUCODE IS NOT NULL
          AND bc.SECUABBR IS NOT NULL
          AND (bc.BONDTYPELEVEL1 = {MTN_TYPE_CODE} OR bc.BONDTYPELEVEL2 = {MTN_TYPE_CODE})
    ),
    issue_ranked AS (
        SELECT
            n.MAINCODE,
            n.ISSUEPRICE,
            n.ACTUALISSUESIZE / 100.0 AS actual_issue_size_yi,
            DATE(n.PUBLICANNOUNCEMENTDATE) AS announcement_date,
            n.ID,
            ROW_NUMBER() OVER (
                PARTITION BY n.MAINCODE
                ORDER BY n.PUBLICANNOUNCEMENTDATE DESC, n.ID DESC
            ) AS rn
        FROM bond_issuenew n
        WHERE n.PUBLICANNOUNCEMENTDATE <= DATE('{AS_OF_DATE}')
    )
    SELECT
        bi.MAINCODE,
        co.INNERCODE,
        co.SECUMARKET,
        co.SECUCODE,
        co.SECUABBR,
        COALESCE(co.ISSUER, bi.ISSUER) AS ISSUER,
        DATE(bi.ISSUEDATE) AS issue_date,
        ir.announcement_date,
        COALESCE(bi.ACTUALISSUESIZE, ir.actual_issue_size_yi) AS issue_size_yi,
        bi.COUPONRATE AS issue_rate_pct,
        bi.MATURITY AS issue_maturity
    FROM bond_basicinfo bi
    JOIN code_ranked co
      ON bi.MAINCODE = co.MAINCODE
     AND co.rn = 1
    LEFT JOIN issue_ranked ir
      ON bi.MAINCODE = ir.MAINCODE
     AND ir.rn = 1
    WHERE bi.ISSUEDATE >= DATE('{Q4_START}')
      AND bi.ISSUEDATE < DATE('{Q4_END_EXCLUSIVE}')
      AND ir.announcement_date IS NOT NULL
      AND COALESCE(bi.ACTUALISSUESIZE, ir.actual_issue_size_yi) IS NOT NULL
      AND bi.COUPONRATE IS NOT NULL
      AND bi.MATURITY > 1
      AND bi.MATURITY <= 5
      AND COALESCE(ir.ISSUEPRICE, bi.ISSUEPRICE) = 100
      {new_issue_term_filter("bi", "co")}
    ORDER BY issue_size_yi DESC, co.SECUCODE ASC;
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=QUERY_TIMEOUT)
    if df.empty:
        return df

    for col in ["MAINCODE", "INNERCODE", "SECUMARKET"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    for col in ["issue_size_yi", "issue_rate_pct", "issue_maturity"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["issue_date"] = pd.to_datetime(df["issue_date"], errors="coerce")
    df["announcement_date"] = pd.to_datetime(df["announcement_date"], errors="coerce")
    return df.dropna(
        subset=["MAINCODE", "issue_date", "announcement_date", "issue_size_yi", "issue_rate_pct", "issue_maturity"]
    )


def fetch_rating_history(maincodes: Iterable[int]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    codes = sorted(set(int(v) for v in maincodes if pd.notna(v)))
    for part in chunks(codes):
        sql = f"""
        SELECT
            MAINCODE,
            CRDESC,
            DATE(INFOPUBLDATE) AS INFOPUBLDATE,
            DATE(CRDATE) AS CRDATE,
            DATE(RATINGEFFECTIVEENDDATE) AS RATINGEFFECTIVEENDDATE,
            DATE(INSERTTIME) AS INSERTTIME,
            UPDATETIME,
            ID
        FROM bond_bdcreditgrading FORCE INDEX (IX_BOND_BDCREDITGRADING)
        WHERE MAINCODE IN ({sql_num_list(part)})
          AND CRTYPECODE = 2
          AND CRSYSTEM = 1
          AND CRSTATUS = 1
          AND CRDESC IN ('AAA', 'AA+');
        """
        frame = query_to_dataframe(sql, max_rows=0, timeout=QUERY_TIMEOUT)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(
            columns=[
                "MAINCODE",
                "CRDESC",
                "INFOPUBLDATE",
                "CRDATE",
                "RATINGEFFECTIVEENDDATE",
                "INSERTTIME",
                "UPDATETIME",
                "ID",
            ]
        )

    ratings = pd.concat(frames, ignore_index=True)
    ratings["MAINCODE"] = pd.to_numeric(ratings["MAINCODE"], errors="coerce").astype("Int64")
    ratings["INFOPUBLDATE"] = pd.to_datetime(ratings["INFOPUBLDATE"], errors="coerce")
    ratings["CRDATE"] = pd.to_datetime(ratings["CRDATE"], errors="coerce")
    ratings["RATINGEFFECTIVEENDDATE"] = pd.to_datetime(ratings["RATINGEFFECTIVEENDDATE"], errors="coerce")
    ratings["INSERTTIME"] = pd.to_datetime(ratings["INSERTTIME"], errors="coerce")
    ratings["UPDATETIME"] = pd.to_datetime(ratings["UPDATETIME"], errors="coerce")
    ratings["ID"] = pd.to_numeric(ratings["ID"], errors="coerce")
    return ratings.dropna(subset=["MAINCODE", "CRDESC", "INFOPUBLDATE"])


def attach_issue_rating(issues: pd.DataFrame, ratings: pd.DataFrame) -> pd.DataFrame:
    out = issues.copy()
    out["CRDESC"] = pd.NA
    if out.empty or ratings.empty:
        return out

    issue_rows = out.reset_index(drop=True).reset_index(names="_issue_row")
    merged = issue_rows[["_issue_row", "MAINCODE", "issue_date"]].merge(ratings, on="MAINCODE", how="inner")
    if merged.empty:
        return out

    issue_exclusive = merged["issue_date"] + pd.Timedelta(days=1)
    valid = merged[
        (merged["INFOPUBLDATE"] < issue_exclusive)
        & (merged["CRDATE"] <= merged["issue_date"])
        & (
            merged["RATINGEFFECTIVEENDDATE"].isna()
            | (merged["RATINGEFFECTIVEENDDATE"] >= merged["issue_date"])
        )
        & (merged["INSERTTIME"].isna() | (merged["INSERTTIME"] < issue_exclusive))
    ].copy()
    if valid.empty:
        return out

    latest = (
        valid.sort_values(["_issue_row", "INFOPUBLDATE", "CRDATE", "UPDATETIME", "ID"])
        .drop_duplicates("_issue_row", keep="last")[["_issue_row", "CRDESC"]]
        .copy()
    )
    out = issue_rows.drop(columns=["CRDESC"], errors="ignore").merge(latest, on="_issue_row", how="left")
    out = out.drop(columns=["_issue_row"])
    return out


def fetch_top_issues() -> pd.DataFrame:
    candidates = fetch_new_issue_candidates()
    if candidates.empty:
        return candidates

    ratings = fetch_rating_history(candidates["MAINCODE"])
    candidates = attach_issue_rating(candidates, ratings)
    candidates = candidates[candidates["CRDESC"].isin(TARGET_RATINGS)].copy()
    if candidates.empty:
        return candidates

    return (
        candidates.sort_values(["issue_size_yi", "SECUCODE"], ascending=[False, True])
        .head(TOP_N)
        .reset_index(drop=True)
    )


def fetch_curve_points(issues: pd.DataFrame) -> pd.DataFrame:
    if issues.empty:
        return pd.DataFrame()

    curve_codes = sorted(CURVE_CODE_BY_RATING.values())
    min_curve_date = (issues["announcement_date"].min() - pd.Timedelta(days=14)).strftime("%Y-%m-%d")
    max_announcement_date = issues["announcement_date"].max().strftime("%Y-%m-%d")
    sql = f"""
    SELECT
        DATE(c.ENDDATE) AS curve_date,
        c.CURVECODE AS curve_code,
        c.CURVETYPE AS curve_name,
        c.YEARSTOMATURITY AS tenor,
        c.YIELD * 100.0 AS curve_yield_pct,
        c.UPDATETIME,
        c.ID
    FROM bond_cbyieldcurve c FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE c.ENDDATE >= DATE('{min_curve_date}')
      AND c.ENDDATE < DATE('{max_announcement_date}')
      AND c.CURVECODE IN ({sql_num_list(curve_codes)})
      AND c.YIELDTYPECODE = 1
      AND c.YIELD IS NOT NULL
      AND c.YEARSTOMATURITY IS NOT NULL;
    """
    curves = query_to_dataframe(sql, max_rows=0, timeout=QUERY_TIMEOUT)
    if curves.empty:
        return curves

    curves["curve_date"] = pd.to_datetime(curves["curve_date"], errors="coerce")
    curves["curve_code"] = pd.to_numeric(curves["curve_code"], errors="coerce").astype("Int64")
    curves["tenor"] = pd.to_numeric(curves["tenor"], errors="coerce")
    curves["curve_yield_pct"] = pd.to_numeric(curves["curve_yield_pct"], errors="coerce")
    curves["UPDATETIME"] = pd.to_datetime(curves["UPDATETIME"], errors="coerce")
    curves["ID"] = pd.to_numeric(curves["ID"], errors="coerce")
    curves = (
        curves.dropna(subset=["curve_date", "curve_code", "tenor", "curve_yield_pct"])
        .sort_values(["curve_code", "curve_date", "tenor", "UPDATETIME", "ID"])
        .drop_duplicates(["curve_code", "curve_date", "tenor"], keep="last")
        .reset_index(drop=True)
    )
    return curves


def interpolate_curve(
    curves: pd.DataFrame,
    rating: str,
    maturity: float,
    announcement_date: pd.Timestamp,
) -> float | None:
    curve_code = CURVE_CODE_BY_RATING.get(str(rating))
    if curve_code is None or pd.isna(maturity) or pd.isna(announcement_date):
        return None

    curve_dates = curves[
        (curves["curve_code"] == curve_code)
        & (curves["curve_date"] < announcement_date)
    ]["curve_date"]
    if curve_dates.empty:
        return None
    benchmark_date = curve_dates.max()

    points = (
        curves[
            (curves["curve_code"] == curve_code)
            & (curves["curve_date"] == benchmark_date)
        ][["tenor", "curve_yield_pct"]]
        .dropna()
        .drop_duplicates("tenor")
        .sort_values("tenor")
    )
    if points.empty:
        return None

    lower = points[points["tenor"] <= float(maturity)].tail(1)
    upper = points[points["tenor"] >= float(maturity)].head(1)
    if lower.empty or upper.empty:
        return None

    x0 = float(lower.iloc[0]["tenor"])
    y0 = float(lower.iloc[0]["curve_yield_pct"])
    x1 = float(upper.iloc[0]["tenor"])
    y1 = float(upper.iloc[0]["curve_yield_pct"])
    if x0 == x1:
        return y0
    return y0 + (float(maturity) - x0) * (y1 - y0) / (x1 - x0)


def build_result() -> pd.DataFrame:
    issues = fetch_top_issues()
    if issues.empty:
        return empty_result()

    curves = fetch_curve_points(issues)
    if curves.empty:
        return empty_result()

    rows: list[dict] = []
    for _, issue in issues.iterrows():
        benchmark = interpolate_curve(
            curves,
            str(issue["CRDESC"]),
            float(issue["issue_maturity"]),
            pd.Timestamp(issue["announcement_date"]),
        )
        spread_bp = (float(issue["issue_rate_pct"]) - benchmark) * 100.0 if benchmark is not None else float("nan")
        rows.append(
            {
                "新券代码": str(issue["SECUCODE"]),
                "简称": issue["SECUABBR"],
                "发行人": issue["ISSUER"],
                "发行规模": float(issue["issue_size_yi"]),
                "债项评级": str(issue["CRDESC"]),
                "发行期限": float(issue["issue_maturity"]),
                "发行利率": float(issue["issue_rate_pct"]),
                "曲线基准收益率": benchmark if benchmark is not None else float("nan"),
                "发行利差": spread_bp,
            }
        )

    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS)


def main() -> pd.DataFrame:
    global _CONNECTION
    try:
        query_to_dataframe("SELECT 1 AS connection_ok", max_rows=0, timeout=30)
        result = build_result()
        return write_result(result)
    finally:
        if _CONNECTION is not None:
            _CONNECTION.close()
            _CONNECTION = None


if __name__ == "__main__":
    main()
