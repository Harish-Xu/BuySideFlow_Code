#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe, shared_connection_session


ISSUE_START_DATE = "2024-10-01"
ISSUE_END_EXCLUSIVE = "2025-01-01"
AS_OF_DATE = "2025-03-31"
AS_OF_EXCLUSIVE = "2025-04-01"
OBSERVATION_NUMBER = 20
PREMIUM_THRESHOLD_BP = 15.0
CHUNK_SIZE = 500

MTN_BOND_NATURE = 14
CURVE_CODE_BY_RATING = {"AA+": 68, "AAA": 69}
OUTPUT_COLUMNS = [
    "债项评级",
    "样本量",
    "平均初始发行溢价bp",
    "平均剩余发行溢价bp",
    "平均收敛bp",
]


def sql_num_list(values: Iterable[int]) -> str:
    return ",".join(str(int(v)) for v in values)


def chunks(values: Iterable[int], size: int = CHUNK_SIZE):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start : start + size]


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def fetch_new_issues() -> pd.DataFrame:
    sql = f"""
    WITH issue_ranked AS (
        SELECT
            n.MAINCODE,
            DATE(n.PUBLICANNOUNCEMENTDATE) AS announcement_date,
            DATE(n.LISTEDDATEINBANK) AS listed_date_in_bank,
            DATE(n.LISTEDDATEINEXCHANGE) AS listed_date_in_exchange,
            n.ISSUEPRICE,
            n.ACTUALISSUESIZE / 100.0 AS issue_size_yi,
            n.ID,
            ROW_NUMBER() OVER (
                PARTITION BY n.MAINCODE
                ORDER BY n.PUBLICANNOUNCEMENTDATE DESC, n.ID DESC
            ) AS rn
        FROM bond_issuenew n
        WHERE n.PUBLICANNOUNCEMENTDATE < '{AS_OF_EXCLUSIVE}'
    )
    SELECT
        b.MAINCODE,
        b.INNERCODE,
        DATE(b.ISSUEDATE) AS issue_date,
        ir.announcement_date,
        DATE(COALESCE(b.LISTEDDATE, ir.listed_date_in_bank, ir.listed_date_in_exchange)) AS listed_date,
        b.COUPONRATE AS issue_rate_pct,
        COALESCE(ir.ISSUEPRICE, b.ISSUEPRICE) AS issue_price,
        b.MATURITY AS issue_maturity,
        COALESCE(ir.issue_size_yi, b.ACTUALISSUESIZE) AS issue_size_yi
    FROM bond_basicinfo b
    JOIN issue_ranked ir
      ON b.MAINCODE = ir.MAINCODE
     AND ir.rn = 1
    WHERE b.ISSUEDATE >= '{ISSUE_START_DATE}'
      AND b.ISSUEDATE < '{ISSUE_END_EXCLUSIVE}'
      AND b.BONDNATURE = {MTN_BOND_NATURE}
      AND b.RATEIFFLOAT = 2
      AND b.COUPONRATE IS NOT NULL
      AND COALESCE(ir.ISSUEPRICE, b.ISSUEPRICE) = 100
      AND COALESCE(ir.issue_size_yi, b.ACTUALISSUESIZE) >= 5
      AND b.MATURITY > 1
      AND b.MATURITY <= 5
      AND COALESCE(b.OPMATURITY, '') NOT LIKE '%N%'
      AND COALESCE(b.BONDFULLNAME, '') NOT LIKE '%永续%'
      AND COALESCE(b.OPTIONTYPE, '') NOT LIKE '%续期%'
      AND ir.announcement_date IS NOT NULL
      AND COALESCE(b.LISTEDDATE, ir.listed_date_in_bank, ir.listed_date_in_exchange) IS NOT NULL
      AND COALESCE(b.LISTEDDATE, ir.listed_date_in_bank, ir.listed_date_in_exchange) < '{AS_OF_EXCLUSIVE}'
    ORDER BY issue_date, b.MAINCODE;
    """
    issues = query_to_dataframe(sql, max_rows=0, timeout=120)
    if issues.empty:
        return issues

    for column in ["MAINCODE", "INNERCODE"]:
        issues[column] = pd.to_numeric(issues[column], errors="coerce").astype("Int64")
    for column in ["issue_rate_pct", "issue_price", "issue_maturity", "issue_size_yi"]:
        issues[column] = pd.to_numeric(issues[column], errors="coerce")
    for column in ["issue_date", "announcement_date", "listed_date"]:
        issues[column] = pd.to_datetime(issues[column], errors="coerce")
    return issues.dropna(
        subset=[
            "MAINCODE",
            "INNERCODE",
            "issue_date",
            "announcement_date",
            "listed_date",
            "issue_rate_pct",
            "issue_maturity",
            "issue_size_yi",
        ]
    ).reset_index(drop=True)


def fetch_rating_history(maincodes: Iterable[int]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    codes = sorted(set(int(value) for value in maincodes if pd.notna(value)))
    for part in chunks(codes):
        sql = f"""
        SELECT
            MAINCODE,
            CRDESC,
            DATE(INFOPUBLDATE) AS rating_pub_date,
            DATE(CRDATE) AS rating_date,
            DATE(RATINGEFFECTIVEENDDATE) AS rating_end_date,
            INSERTTIME AS insert_time,
            UPDATETIME AS update_time,
            ID
        FROM bond_bdcreditgrading FORCE INDEX (IX_BOND_BDCREDITGRADING)
        WHERE MAINCODE IN ({sql_num_list(part)})
          AND CRTYPECODE = 2
          AND CRSYSTEM = 1
          AND CRSTATUS = 1
          AND CRDESC IS NOT NULL
          AND INFOPUBLDATE < '{AS_OF_EXCLUSIVE}'
          AND CRDATE < '{AS_OF_EXCLUSIVE}';
        """
        frame = query_to_dataframe(sql, max_rows=0, timeout=120)
        if not frame.empty:
            frames.append(frame)

    if not frames:
        return pd.DataFrame()

    ratings = pd.concat(frames, ignore_index=True)
    ratings["MAINCODE"] = pd.to_numeric(ratings["MAINCODE"], errors="coerce").astype("Int64")
    ratings["ID"] = pd.to_numeric(ratings["ID"], errors="coerce")
    for column in ["rating_pub_date", "rating_date", "rating_end_date", "insert_time", "update_time"]:
        ratings[column] = pd.to_datetime(ratings[column], errors="coerce")
    return ratings.dropna(subset=["MAINCODE", "CRDESC", "rating_pub_date", "rating_date"])


def attach_issue_rating(issues: pd.DataFrame, ratings: pd.DataFrame) -> pd.DataFrame:
    if issues.empty or ratings.empty:
        return pd.DataFrame(columns=[*issues.columns, "issue_rating"])

    issue_rows = issues.reset_index(drop=True).reset_index(names="_issue_row")
    merged = issue_rows[["_issue_row", "MAINCODE", "issue_date"]].merge(
        ratings,
        on="MAINCODE",
        how="inner",
    )
    if merged.empty:
        return pd.DataFrame(columns=[*issues.columns, "issue_rating"])

    issue_day_end = merged["issue_date"] + pd.Timedelta(days=1)
    valid = merged[
        (merged["rating_pub_date"] <= merged["issue_date"])
        & (merged["rating_date"] <= merged["issue_date"])
        & (merged["insert_time"].isna() | (merged["insert_time"] < issue_day_end))
        & (merged["rating_end_date"].isna() | (merged["rating_end_date"] >= merged["issue_date"]))
    ].copy()
    if valid.empty:
        return pd.DataFrame(columns=[*issues.columns, "issue_rating"])

    latest = (
        valid.sort_values(
            ["_issue_row", "rating_pub_date", "rating_date", "insert_time", "update_time", "ID"],
            na_position="first",
        )
        .drop_duplicates("_issue_row", keep="last")[["_issue_row", "CRDESC"]]
        .rename(columns={"CRDESC": "issue_rating"})
    )
    return (
        issue_rows.merge(latest, on="_issue_row", how="inner")
        .drop(columns="_issue_row")
        .reset_index(drop=True)
    )


def fetch_curve_points(issues: pd.DataFrame) -> pd.DataFrame:
    if issues.empty:
        return pd.DataFrame()

    min_curve_date = (issues["announcement_date"].min() - pd.Timedelta(days=14)).date().isoformat()
    curve_codes = sorted(CURVE_CODE_BY_RATING.values())
    sql = f"""
    SELECT
        DATE(ENDDATE) AS curve_date,
        CURVECODE AS curve_code,
        YEARSTOMATURITY AS tenor,
        YIELD * 100.0 AS curve_yield_pct,
        UPDATETIME AS update_time,
        ID
    FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE ENDDATE >= '{min_curve_date}'
      AND ENDDATE < '{AS_OF_EXCLUSIVE}'
      AND CURVECODE IN ({sql_num_list(curve_codes)})
      AND YIELDTYPECODE = 1
      AND YEARSTOMATURITY > 0
      AND YEARSTOMATURITY <= 5
      AND YIELD IS NOT NULL
      AND UPDATETIME < DATE_ADD(DATE(ENDDATE), INTERVAL 1 DAY);
    """
    curves = query_to_dataframe(sql, max_rows=0, timeout=120)
    if curves.empty:
        return curves

    curves["curve_date"] = pd.to_datetime(curves["curve_date"], errors="coerce")
    curves["curve_code"] = pd.to_numeric(curves["curve_code"], errors="coerce").astype("Int64")
    curves["tenor"] = pd.to_numeric(curves["tenor"], errors="coerce")
    curves["curve_yield_pct"] = pd.to_numeric(curves["curve_yield_pct"], errors="coerce")
    curves["update_time"] = pd.to_datetime(curves["update_time"], errors="coerce")
    curves["ID"] = pd.to_numeric(curves["ID"], errors="coerce")
    return (
        curves.dropna(subset=["curve_date", "curve_code", "tenor", "curve_yield_pct", "update_time"])
        .sort_values(["curve_date", "curve_code", "tenor", "update_time", "ID"])
        .drop_duplicates(["curve_date", "curve_code", "tenor"], keep="last")
        .reset_index(drop=True)
    )


def interpolate_curve(
    curves: pd.DataFrame,
    curve_date: pd.Timestamp,
    rating: str,
    maturity: float,
) -> float | None:
    curve_code = CURVE_CODE_BY_RATING.get(str(rating))
    if curve_code is None or pd.isna(curve_date) or pd.isna(maturity) or float(maturity) <= 0:
        return None

    points = (
        curves[
            (curves["curve_date"] == pd.Timestamp(curve_date))
            & (curves["curve_code"] == curve_code)
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


def initial_curve_value(
    curves: pd.DataFrame,
    announcement_date: pd.Timestamp,
    rating: str,
    maturity: float,
) -> tuple[pd.Timestamp, float] | None:
    curve_code = CURVE_CODE_BY_RATING.get(str(rating))
    if curve_code is None:
        return None

    dates = (
        curves[
            (curves["curve_code"] == curve_code)
            & (curves["curve_date"] < pd.Timestamp(announcement_date))
        ]["curve_date"]
        .drop_duplicates()
        .sort_values(ascending=False)
    )
    for curve_date in dates:
        curve_yield = interpolate_curve(curves, curve_date, rating, maturity)
        if curve_yield is not None:
            return pd.Timestamp(curve_date), curve_yield
    return None


def build_signal_pool(issues: pd.DataFrame, curves: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for _, issue in issues.iterrows():
        benchmark = initial_curve_value(
            curves,
            pd.Timestamp(issue["announcement_date"]),
            str(issue["issue_rating"]),
            float(issue["issue_maturity"]),
        )
        if benchmark is None:
            continue

        initial_curve_date, initial_curve_yield_pct = benchmark
        initial_premium_bp = (float(issue["issue_rate_pct"]) - initial_curve_yield_pct) * 100.0
        if initial_premium_bp <= PREMIUM_THRESHOLD_BP:
            continue

        rows.append(
            {
                **issue.to_dict(),
                "initial_curve_date": initial_curve_date,
                "initial_curve_yield_pct": initial_curve_yield_pct,
                "initial_premium_bp": initial_premium_bp,
            }
        )
    return pd.DataFrame(rows)


def fetch_valuation_history(signals: pd.DataFrame) -> pd.DataFrame:
    if signals.empty:
        return pd.DataFrame()

    frames: list[pd.DataFrame] = []
    innercodes = sorted(set(int(value) for value in signals["INNERCODE"] if pd.notna(value)))
    min_listed_date = signals["listed_date"].min().date().isoformat()
    for part in chunks(innercodes, size=200):
        sql = f"""
        SELECT
            INNERCODE,
            DATE(ENDDATE) AS valuation_date,
            VPYIELD AS valuation_yield_pct,
            TRUEREMAINMATURITY AS remaining_maturity,
            INSERTTIME AS insert_time,
            UPDATETIME AS update_time,
            ID
        FROM bond_cbvaluationall FORCE INDEX (IX_BOND_CBVALUATIONALL)
        WHERE INNERCODE IN ({sql_num_list(part)})
          AND ENDDATE >= '{min_listed_date}'
          AND ENDDATE < '{AS_OF_EXCLUSIVE}'
          AND CREDIBILITYCODE = 1
          AND VPYIELD IS NOT NULL
          AND (INSERTTIME IS NULL OR INSERTTIME < DATE_ADD(DATE(ENDDATE), INTERVAL 1 DAY))
          AND UPDATETIME < DATE_ADD(DATE(ENDDATE), INTERVAL 1 DAY);
        """
        frame = query_to_dataframe(sql, max_rows=0, timeout=120)
        if not frame.empty:
            frames.append(frame)

    if not frames:
        return pd.DataFrame()

    valuations = pd.concat(frames, ignore_index=True)
    valuations["INNERCODE"] = pd.to_numeric(valuations["INNERCODE"], errors="coerce").astype("Int64")
    valuations["valuation_date"] = pd.to_datetime(valuations["valuation_date"], errors="coerce")
    valuations["valuation_yield_pct"] = pd.to_numeric(valuations["valuation_yield_pct"], errors="coerce")
    valuations["remaining_maturity"] = pd.to_numeric(valuations["remaining_maturity"], errors="coerce")
    valuations["insert_time"] = pd.to_datetime(valuations["insert_time"], errors="coerce")
    valuations["update_time"] = pd.to_datetime(valuations["update_time"], errors="coerce")
    valuations["ID"] = pd.to_numeric(valuations["ID"], errors="coerce")
    return (
        valuations.dropna(subset=["INNERCODE", "valuation_date", "valuation_yield_pct", "update_time"])
        .sort_values(["INNERCODE", "valuation_date", "update_time", "insert_time", "ID"])
        .drop_duplicates(["INNERCODE", "valuation_date"], keep="last")
        .reset_index(drop=True)
    )


def build_detail(
    signals: pd.DataFrame,
    valuations: pd.DataFrame,
    curves: pd.DataFrame,
) -> pd.DataFrame:
    if signals.empty or valuations.empty or curves.empty:
        return pd.DataFrame()

    rows: list[dict] = []
    for _, signal in signals.iterrows():
        history = (
            valuations[
                (valuations["INNERCODE"] == int(signal["INNERCODE"]))
                & (valuations["valuation_date"] >= pd.Timestamp(signal["listed_date"]))
                & (valuations["valuation_date"] <= pd.Timestamp(AS_OF_DATE))
            ]
            .sort_values("valuation_date")
            .reset_index(drop=True)
        )
        if len(history) < OBSERVATION_NUMBER:
            continue

        observation = history.iloc[OBSERVATION_NUMBER - 1]
        remaining_maturity = observation["remaining_maturity"]
        if pd.isna(remaining_maturity) or float(remaining_maturity) <= 0:
            continue

        observation_curve_yield_pct = interpolate_curve(
            curves,
            pd.Timestamp(observation["valuation_date"]),
            str(signal["issue_rating"]),
            float(remaining_maturity),
        )
        if observation_curve_yield_pct is None:
            continue

        remaining_premium_bp = (
            float(observation["valuation_yield_pct"]) - observation_curve_yield_pct
        ) * 100.0
        convergence_bp = float(signal["initial_premium_bp"]) - remaining_premium_bp
        rows.append(
            {
                "MAINCODE": int(signal["MAINCODE"]),
                "INNERCODE": int(signal["INNERCODE"]),
                "债项评级": str(signal["issue_rating"]),
                "发行日": signal["issue_date"],
                "发行公告日": signal["announcement_date"],
                "初始曲线日": signal["initial_curve_date"],
                "上市日": signal["listed_date"],
                "第20个估值日": observation["valuation_date"],
                "发行期限": float(signal["issue_maturity"]),
                "观察日剩余期限": float(remaining_maturity),
                "发行利率": float(signal["issue_rate_pct"]),
                "初始曲线收益率": float(signal["initial_curve_yield_pct"]),
                "观察日估值收益率": float(observation["valuation_yield_pct"]),
                "观察日曲线收益率": observation_curve_yield_pct,
                "初始发行溢价bp": float(signal["initial_premium_bp"]),
                "剩余发行溢价bp": remaining_premium_bp,
                "收敛bp": convergence_bp,
            }
        )
    return pd.DataFrame(rows)


def build_result(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return empty_result()

    result = (
        detail.groupby("债项评级", as_index=False)
        .agg(
            样本量=("MAINCODE", "nunique"),
            **{
                "平均初始发行溢价bp": ("初始发行溢价bp", "mean"),
                "平均剩余发行溢价bp": ("剩余发行溢价bp", "mean"),
                "平均收敛bp": ("收敛bp", "mean"),
            },
        )
    )
    for column in OUTPUT_COLUMNS[2:]:
        result[column] = result[column].round(2)
    rating_order = {"AAA": 0, "AA+": 1}
    result["_rating_order"] = result["债项评级"].map(rating_order).fillna(99)
    return result.sort_values(["_rating_order", "债项评级"])[OUTPUT_COLUMNS].reset_index(drop=True)


def build_all() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    issues = fetch_new_issues()
    if issues.empty:
        return issues, pd.DataFrame(), pd.DataFrame(), empty_result()

    ratings = fetch_rating_history(issues["MAINCODE"])
    rated_issues = attach_issue_rating(issues, ratings)
    rated_issues = rated_issues[rated_issues["issue_rating"].isin(CURVE_CODE_BY_RATING)].reset_index(drop=True)
    if rated_issues.empty:
        return rated_issues, pd.DataFrame(), pd.DataFrame(), empty_result()

    curves = fetch_curve_points(rated_issues)
    signals = build_signal_pool(rated_issues, curves)
    if signals.empty:
        return rated_issues, signals, pd.DataFrame(), empty_result()

    valuations = fetch_valuation_history(signals)
    detail = build_detail(signals, valuations, curves)
    return rated_issues, signals, detail, build_result(detail)


def main() -> pd.DataFrame:
    with shared_connection_session():
        _, _, _, result = build_all()
    out_path = Path(__file__).with_name("result.csv")
    result.to_csv(out_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(result.to_csv(index=False, float_format="%.2f").strip())
    return result


if __name__ == "__main__":
    main()
