#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import get_db_connector


DEC_DATE = "2025-12-31"
NOV_DATE = "2025-11-30"
MIN_REMAINING_YEARS = 1.0
MAX_REMAINING_YEARS = 3.0
MIN_GROUP_COUNT = 5
RESULT_COLUMNS = ["省份", "匹配只数", "12月末利差中位数", "11月末利差中位数", "利差变化"]

PROVINCE_BY_PREFIX = {
    11: "北京",
    12: "天津",
    13: "河北",
    14: "山西",
    15: "内蒙古",
    21: "辽宁",
    22: "吉林",
    23: "黑龙江",
    31: "上海",
    32: "江苏",
    33: "浙江",
    34: "安徽",
    35: "福建",
    36: "江西",
    37: "山东",
    41: "河南",
    42: "湖北",
    43: "湖南",
    44: "广东",
    45: "广西",
    46: "海南",
    50: "重庆",
    51: "四川",
    52: "贵州",
    53: "云南",
    54: "西藏",
    61: "陕西",
    62: "甘肃",
    63: "青海",
    64: "宁夏",
    65: "新疆",
}

ADDR_FALLBACK = [
    ("内蒙古", "内蒙古"),
    ("黑龙江", "黑龙江"),
    ("广西", "广西"),
    ("西藏", "西藏"),
    ("宁夏", "宁夏"),
    ("新疆", "新疆"),
    ("北京", "北京"),
    ("天津", "天津"),
    ("上海", "上海"),
    ("重庆", "重庆"),
    ("河北", "河北"),
    ("山西", "山西"),
    ("辽宁", "辽宁"),
    ("吉林", "吉林"),
    ("江苏", "江苏"),
    ("浙江", "浙江"),
    ("安徽", "安徽"),
    ("福建", "福建"),
    ("江西", "江西"),
    ("山东", "山东"),
    ("河南", "河南"),
    ("湖北", "湖北"),
    ("湖南", "湖南"),
    ("广东", "广东"),
    ("海南", "海南"),
    ("四川", "四川"),
    ("贵州", "贵州"),
    ("云南", "云南"),
    ("陕西", "陕西"),
    ("甘肃", "甘肃"),
    ("青海", "青海"),
    ("深圳", "广东"),
    ("宁波", "浙江"),
    ("厦门", "福建"),
    ("青岛", "山东"),
    ("大连", "辽宁"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AAA 城投债 2025-12 月末相对 11 月末省份利差变化")
    parser.add_argument("--output-path", default=str(Path(__file__).with_name("result.csv")))
    return parser.parse_args()


def exec_df(connector, conn, sql: str, timeout: int = 600) -> pd.DataFrame:
    return connector.execute_sql_to_dataframe_on_connection(conn, sql, max_rows=0, timeout=timeout)


def sql_date_add_one(day: str) -> str:
    return (pd.Timestamp(day) + pd.Timedelta(days=1)).date().isoformat()


def create_temp_tables(connector, conn) -> None:
    for table in [
        "tmp_b056_city_bonds",
        "tmp_b056_maincodes",
        "tmp_b056_dec_rating",
        "tmp_b056_nov_rating",
    ]:
        exec_df(connector, conn, f"DROP TEMPORARY TABLE IF EXISTS {table}")

    dec_next = sql_date_add_one(DEC_DATE)
    exec_df(
        connector,
        conn,
        f"""
        CREATE TEMPORARY TABLE tmp_b056_city_bonds AS
        SELECT DISTINCT
            CAST(bc.MAINCODE AS UNSIGNED) AS main_code,
            CAST(bc.INNERCODE AS UNSIGNED) AS inner_code,
            bc.SECUCODE AS bond_code,
            bc.SECUABBR AS bond_name,
            bc.ISSUER AS issuer,
            bc.COMPANYCODE AS company_code,
            ia.REGCITY AS regcity,
            ia.REGADDR AS regaddr
        FROM bond_conceptnature cn FORCE INDEX (IX_BOND_CONCEPTNATURE_COMPOSITE)
        JOIN bond_code bc FORCE INDEX (IX_BOND_CODE)
          ON bc.INNERCODE = cn.INNERCODE
        LEFT JOIN lc_instiarchive ia FORCE INDEX (IX_LC_INSTIARCHIVE)
          ON ia.COMPANYCODE = bc.COMPANYCODE
         AND (ia.STARTDATE IS NULL OR ia.STARTDATE < DATE('{dec_next}'))
         AND (ia.CLOSEDATE IS NULL OR ia.CLOSEDATE >= DATE('{DEC_DATE}'))
        WHERE cn.CONCEPTTYPE = 40
          AND cn.CONNATURE = 4041
          AND (cn.BEGINDATE IS NULL OR cn.BEGINDATE < DATE('{dec_next}'))
          AND (cn.ENDDATE IS NULL OR cn.ENDDATE > DATE('{DEC_DATE}'))
          AND cn.INSERTTIME < DATE('{dec_next}')
          AND bc.MAINCODE IS NOT NULL
          AND bc.INNERCODE IS NOT NULL
          AND (bc.LISTEDDATE IS NULL OR bc.LISTEDDATE < DATE('{dec_next}'))
          AND (bc.DELISTDATE IS NULL OR bc.DELISTDATE >= DATE('{DEC_DATE}'))
          AND (bc.INTERESTENDDATE IS NULL OR bc.INTERESTENDDATE >= DATE('{dec_next}'));
        """,
    )
    exec_df(
        connector,
        conn,
        """
        ALTER TABLE tmp_b056_city_bonds
        ADD INDEX idx_inner (inner_code),
        ADD INDEX idx_main (main_code);
        """,
    )
    exec_df(
        connector,
        conn,
        """
        CREATE TEMPORARY TABLE tmp_b056_maincodes AS
        SELECT DISTINCT main_code
        FROM tmp_b056_city_bonds;
        """,
    )
    exec_df(connector, conn, "ALTER TABLE tmp_b056_maincodes ADD PRIMARY KEY (main_code);")
    create_rating_table(connector, conn, "tmp_b056_dec_rating", DEC_DATE)
    create_rating_table(connector, conn, "tmp_b056_nov_rating", NOV_DATE)


def fetch_reference_valuation_date(connector, conn, ref_date: str) -> str:
    next_day = sql_date_add_one(ref_date)
    df = exec_df(
        connector,
        conn,
        f"""
        SELECT DATE(ENDDATE) AS valuation_date
        FROM bond_cbvaluationall FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
        WHERE ENDDATE < DATE('{next_day}')
          AND CREDIBILITYCODE = 1
          AND VPYIELD IS NOT NULL
          AND TRUEREMAINMATURITY IS NOT NULL
          AND (INSERTTIME IS NULL OR INSERTTIME < DATE('{next_day}'))
          AND UPDATETIME < DATE('{next_day}')
        ORDER BY ENDDATE DESC
        LIMIT 1;
        """,
        timeout=180,
    )
    if df.empty or pd.isna(df.loc[0, "valuation_date"]):
        raise ValueError(f"{ref_date} 之前无中债推荐估值日")
    return pd.Timestamp(df.loc[0, "valuation_date"]).date().isoformat()


def fetch_valuation(connector, conn, valuation_date: str, ref_date: str, filter_remaining: bool) -> pd.DataFrame:
    next_day = sql_date_add_one(valuation_date)
    cutoff_day = sql_date_add_one(ref_date)
    remaining_filter = (
        f"AND v.TRUEREMAINMATURITY > {MIN_REMAINING_YEARS} "
        f"AND v.TRUEREMAINMATURITY <= {MAX_REMAINING_YEARS}"
        if filter_remaining
        else "AND v.TRUEREMAINMATURITY > 0"
    )
    sql = f"""
    SELECT
        main_code,
        inner_code,
        bond_code,
        bond_name,
        issuer,
        company_code,
        regcity,
        regaddr,
        valuation_date,
        valuation_yield_pct,
        remaining_years
    FROM (
        SELECT
            cb.main_code,
            cb.inner_code,
            cb.bond_code,
            cb.bond_name,
            cb.issuer,
            cb.company_code,
            cb.regcity,
            cb.regaddr,
            DATE(v.ENDDATE) AS valuation_date,
            v.VPYIELD AS valuation_yield_pct,
            v.TRUEREMAINMATURITY AS remaining_years,
            ROW_NUMBER() OVER (
                PARTITION BY cb.main_code
                ORDER BY
                    v.CREDIBILITYCODE ASC,
                    COALESCE(cb.bond_code, '') ASC,
                    cb.inner_code ASC,
                    v.UPDATETIME DESC,
                    v.INSERTTIME DESC,
                    v.ID DESC
            ) AS rn
        FROM tmp_b056_city_bonds cb
        JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL)
          ON v.INNERCODE = cb.inner_code
        WHERE v.ENDDATE >= DATE('{valuation_date}')
          AND v.ENDDATE < DATE('{next_day}')
          AND v.CREDIBILITYCODE = 1
          AND v.VPYIELD IS NOT NULL
          AND v.TRUEREMAINMATURITY IS NOT NULL
          AND (v.INSERTTIME IS NULL OR v.INSERTTIME < DATE('{cutoff_day}'))
          AND v.UPDATETIME < DATE('{cutoff_day}')
          {remaining_filter}
    ) ranked
    WHERE rn = 1;
    """
    return exec_df(connector, conn, sql)


def create_rating_table(connector, conn, table_name: str, ref_date: str) -> None:
    next_day = sql_date_add_one(ref_date)
    exec_df(
        connector,
        conn,
        f"""
        CREATE TEMPORARY TABLE {table_name} AS
        SELECT main_code, rating_desc
        FROM (
            SELECT
                CAST(r.MAINCODE AS UNSIGNED) AS main_code,
                r.CRDESC AS rating_desc,
                ROW_NUMBER() OVER (
                    PARTITION BY r.MAINCODE
                    ORDER BY
                        COALESCE(r.INFOPUBLDATE, r.CRDATE) DESC,
                        r.CRDATE DESC,
                        r.ID DESC
                ) AS rn
            FROM bond_bdcreditgrading r FORCE INDEX (IX_BOND_BDCREDITGRADING_Q047)
            JOIN tmp_b056_maincodes m
              ON m.main_code = r.MAINCODE
            WHERE COALESCE(r.INFOPUBLDATE, r.CRDATE) < DATE('{next_day}')
              AND r.CRTYPECODE = 2
              AND r.CRSYSTEM = 1
              AND r.CRDESC IS NOT NULL
              AND (r.CRSTATUS IS NULL OR r.CRSTATUS <> 3)
              AND (r.RATINGEFFECTIVEENDDATE IS NULL OR r.RATINGEFFECTIVEENDDATE >= DATE('{ref_date}'))
        ) ranked
        WHERE rn = 1
          AND rating_desc = 'AAA';
        """,
    )
    exec_df(connector, conn, f"ALTER TABLE {table_name} ADD PRIMARY KEY (main_code);")


def fetch_rating_maincodes(connector, conn, table_name: str) -> set[int]:
    df = exec_df(connector, conn, f"SELECT main_code FROM {table_name};")
    if df.empty:
        return set()
    return set(pd.to_numeric(df["main_code"], errors="coerce").dropna().astype("int64"))


def build_matched_panel(dec_val: pd.DataFrame, nov_val: pd.DataFrame, dec_rated: set[int], nov_rated: set[int]) -> pd.DataFrame:
    if dec_val.empty or nov_val.empty:
        return pd.DataFrame()
    dec_val = dec_val.copy()
    nov_val = nov_val.copy()
    dec_val["main_code"] = pd.to_numeric(dec_val["main_code"], errors="coerce").astype("Int64")
    nov_val["main_code"] = pd.to_numeric(nov_val["main_code"], errors="coerce").astype("Int64")
    eligible_codes = dec_rated.intersection(nov_rated)
    dec_val = dec_val[dec_val["main_code"].astype("int64").isin(eligible_codes)].copy()
    nov_val = nov_val[nov_val["main_code"].astype("int64").isin(eligible_codes)].copy()
    merged = dec_val.merge(
        nov_val[["main_code", "inner_code", "valuation_date", "valuation_yield_pct", "remaining_years"]],
        on="main_code",
        how="inner",
        suffixes=("_dec", "_nov"),
    )
    if merged.empty:
        return merged
    return merged.rename(
        columns={
            "inner_code_dec": "dec_inner_code",
            "valuation_date_dec": "dec_valuation_date",
            "valuation_yield_pct_dec": "dec_valuation_yield_pct",
            "remaining_years_dec": "dec_remaining_years",
            "inner_code_nov": "nov_inner_code",
            "valuation_date_nov": "nov_valuation_date",
            "valuation_yield_pct_nov": "nov_valuation_yield_pct",
            "remaining_years_nov": "nov_remaining_years",
        }
    )


def fetch_curve(connector, conn, date_cutoffs: dict[str, str]) -> pd.DataFrame:
    if not date_cutoffs:
        return pd.DataFrame(columns=["curve_date", "years_to_maturity", "curve_yield"])
    requested_sql = "\nUNION ALL\n".join(
        f"SELECT DATE('{day}') AS curve_date, DATE('{cutoff}') AS cutoff_day"
        for day, cutoff in sorted(date_cutoffs.items())
    )
    sql = f"""
    WITH requested_dates AS (
        {requested_sql}
    ),
    ranked AS (
    SELECT
        DATE(c.ENDDATE) AS curve_date,
        c.YEARSTOMATURITY AS years_to_maturity,
        c.YIELD AS curve_yield,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(c.ENDDATE), c.YEARSTOMATURITY
            ORDER BY c.UPDATETIME DESC, c.ID DESC
        ) AS rn
    FROM requested_dates rd
    JOIN bond_cbyieldcurve c FORCE INDEX (IX_BOND_CBYIELDCURVE)
      ON c.ENDDATE >= rd.curve_date
     AND c.ENDDATE < DATE_ADD(rd.curve_date, INTERVAL 1 DAY)
    WHERE c.CURVECODE = 10
      AND c.YIELDTYPECODE = 1
      AND c.YEARSTOMATURITY IS NOT NULL
      AND c.YIELD IS NOT NULL
      AND c.UPDATETIME < rd.cutoff_day
    )
    SELECT curve_date, years_to_maturity, curve_yield
    FROM ranked
    WHERE rn = 1
    ORDER BY curve_date, years_to_maturity;
    """
    return exec_df(connector, conn, sql)


def province_from_registration(regcity: object, regaddr: object) -> str | None:
    if pd.notna(regcity):
        try:
            code = int(float(regcity))
            prefix = code // 10000
            if prefix in PROVINCE_BY_PREFIX:
                return PROVINCE_BY_PREFIX[prefix]
        except (TypeError, ValueError):
            pass

    text = "" if pd.isna(regaddr) else str(regaddr).strip()
    for key, province in ADDR_FALLBACK:
        if text.startswith(key) or key in text[:10]:
            return province
    return None


def build_curve_lookup(curve: pd.DataFrame) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    if curve.empty:
        raise ValueError("国债曲线数据为空")
    curve = curve.copy()
    curve["curve_date"] = pd.to_datetime(curve["curve_date"]).dt.strftime("%Y-%m-%d")
    curve["years_to_maturity"] = pd.to_numeric(curve["years_to_maturity"], errors="coerce")
    curve["curve_yield"] = pd.to_numeric(curve["curve_yield"], errors="coerce")
    curve = curve.dropna(subset=["curve_date", "years_to_maturity", "curve_yield"]).copy()

    # bond_cbvaluationall.VPYIELD is documented as percent. In this database the
    # ChinaBond curve YIELD is stored as decimal yield, so convert it to percent.
    if curve["curve_yield"].abs().max() < 1:
        curve["curve_yield"] = curve["curve_yield"] * 100.0

    lookup: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for day, group in curve.groupby("curve_date", sort=True):
        group = group.drop_duplicates("years_to_maturity").sort_values("years_to_maturity")
        if len(group) >= 2:
            lookup[day] = (
                group["years_to_maturity"].to_numpy(dtype=float),
                group["curve_yield"].to_numpy(dtype=float),
            )
    if not lookup:
        raise ValueError("国债曲线缺少可插值期限点")
    return lookup


def interpolate_curve(lookup: dict[str, tuple[np.ndarray, np.ndarray]], curve_date: object, maturity: object) -> float:
    if pd.isna(curve_date) or pd.isna(maturity):
        return np.nan
    day = pd.Timestamp(curve_date).strftime("%Y-%m-%d")
    if day not in lookup:
        return np.nan
    tenors, yields = lookup[day]
    x = float(maturity)
    if x < float(tenors[0]) or x > float(tenors[-1]):
        return np.nan
    return float(np.interp(x, tenors, yields))


def compute_panel_spreads(panel: pd.DataFrame, curve_lookup: dict[str, tuple[np.ndarray, np.ndarray]]) -> pd.DataFrame:
    if panel.empty:
        return panel
    data = panel.copy()
    for col in ["dec_valuation_yield_pct", "nov_valuation_yield_pct", "dec_remaining_years", "nov_remaining_years"]:
        data[col] = pd.to_numeric(data[col], errors="coerce")

    data["省份"] = [
        province_from_registration(regcity, regaddr)
        for regcity, regaddr in zip(data["regcity"], data["regaddr"])
    ]
    data["dec_treasury_yield_pct"] = [
        interpolate_curve(curve_lookup, day, maturity)
        for day, maturity in zip(data["dec_valuation_date"], data["dec_remaining_years"])
    ]
    data["nov_treasury_yield_pct"] = [
        interpolate_curve(curve_lookup, day, maturity)
        for day, maturity in zip(data["nov_valuation_date"], data["nov_remaining_years"])
    ]
    data["dec_spread_bp"] = (data["dec_valuation_yield_pct"] - data["dec_treasury_yield_pct"]) * 100.0
    data["nov_spread_bp"] = (data["nov_valuation_yield_pct"] - data["nov_treasury_yield_pct"]) * 100.0
    return data.dropna(subset=["省份", "dec_spread_bp", "nov_spread_bp"]).reset_index(drop=True)


def summarize(panel: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    if len(panel) >= MIN_GROUP_COUNT:
        dec_median = float(panel["dec_spread_bp"].median())
        nov_median = float(panel["nov_spread_bp"].median())
        rows.append(
            {
                "省份": "全国",
                "匹配只数": int(len(panel)),
                "12月末利差中位数": dec_median,
                "11月末利差中位数": nov_median,
                "利差变化": dec_median - nov_median,
            }
        )

    province_rows = []
    for province, group in panel.groupby("省份", sort=True):
        if len(group) < MIN_GROUP_COUNT:
            continue
        dec_median = float(group["dec_spread_bp"].median())
        nov_median = float(group["nov_spread_bp"].median())
        province_rows.append(
            {
                "省份": province,
                "匹配只数": int(len(group)),
                "12月末利差中位数": dec_median,
                "11月末利差中位数": nov_median,
                "利差变化": dec_median - nov_median,
            }
        )

    province_rows = sorted(province_rows, key=lambda row: (row["利差变化"], row["省份"]))
    result = pd.DataFrame(rows + province_rows, columns=RESULT_COLUMNS)
    if result.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    for col in ["12月末利差中位数", "11月末利差中位数", "利差变化"]:
        result[col] = result[col].map(lambda value: f"{float(value):.2f}")
    return result


def main() -> None:
    args = parse_args()
    output_path = Path(args.output_path)

    connector = get_db_connector()
    with connector.connection() as conn:
        create_temp_tables(connector, conn)
        dec_valuation_date = fetch_reference_valuation_date(connector, conn, DEC_DATE)
        nov_valuation_date = fetch_reference_valuation_date(connector, conn, NOV_DATE)
        dec_val = fetch_valuation(connector, conn, dec_valuation_date, DEC_DATE, filter_remaining=True)
        nov_val = fetch_valuation(connector, conn, nov_valuation_date, NOV_DATE, filter_remaining=False)
        dec_rated = fetch_rating_maincodes(connector, conn, "tmp_b056_dec_rating")
        nov_rated = fetch_rating_maincodes(connector, conn, "tmp_b056_nov_rating")
        panel = build_matched_panel(dec_val, nov_val, dec_rated, nov_rated)
        valuation_date_cutoffs = {}
        if not panel.empty:
            dec_cutoff = sql_date_add_one(DEC_DATE)
            nov_cutoff = sql_date_add_one(NOV_DATE)
            for day in pd.to_datetime(panel["dec_valuation_date"]).dt.strftime("%Y-%m-%d").unique():
                valuation_date_cutoffs[day] = dec_cutoff
            for day in pd.to_datetime(panel["nov_valuation_date"]).dt.strftime("%Y-%m-%d").unique():
                valuation_date_cutoffs[day] = nov_cutoff
        curve = fetch_curve(connector, conn, valuation_date_cutoffs)

    if panel.empty:
        result = pd.DataFrame(columns=RESULT_COLUMNS)
    else:
        curve_lookup = build_curve_lookup(curve)
        panel = compute_panel_spreads(panel, curve_lookup)
        result = summarize(panel)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
