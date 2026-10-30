#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd

from sweagent.text2sql.db_connector import get_db_connector


ANCHOR_DAY = "2024-09-03"
AS_OF_NEXT_DAY = "2024-09-04"
HISTORY_START = "2023-08-01"
HISTORY_END = "2024-08-31"
OPEN_FUND_TYPES_SQL = "2, 3, 4, 7, 8"
MIN_WIN_MONTHS = 10
LEVEL_MONTHS = pd.period_range("2023-08", "2024-08", freq="M")
RETURN_MONTHS = pd.period_range("2023-09", "2024-08", freq="M")


FUND_POOL_SQL = f"""
WITH params AS (
    SELECT
        DATE('{ANCHOR_DAY}') AS anchor_day,
        DATE('{AS_OF_NEXT_DAY}') AS as_of_next_day,
        DATE_SUB(DATE('{ANCHOR_DAY}'), INTERVAL 1 YEAR) AS min_establish_day,
        DATE('{HISTORY_START}') AS history_start,
        DATE('{HISTORY_END}') AS history_end
),
type_asof AS (
    SELECT
        t.INNERCODE,
        t.SECASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS rn
    FROM mf_jyfundtype t
    JOIN params p
      ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= p.anchor_day
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.anchor_day)
      AND t.INSERTTIME < p.as_of_next_day
),
fund_pool AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        sm.SECUABBR AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN type_asof ta
      ON ta.INNERCODE = fa.INNERCODE
     AND ta.rn = 1
    JOIN params p
      ON 1 = 1
    WHERE ta.SECASSETCATCODE = 1201
      AND fa.TYPE IN ({OPEN_FUND_TYPES_SQL})
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= p.min_establish_day
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.anchor_day)
      AND (fa.INSERTTIME IS NULL OR fa.INSERTTIME < p.as_of_next_day)
)
SELECT INNERCODE, fund_code, fund_name
FROM fund_pool
ORDER BY fund_code
"""


FUND_MONTH_END_SQL = f"""
SELECT
    n.INNERCODE,
    'fund' AS series_type,
    DATE_FORMAT(n.TRADINGDAY, '%Y-%m') AS month_key,
    SUBSTRING_INDEX(
        GROUP_CONCAT(
            CONCAT(DATE_FORMAT(n.TRADINGDAY, '%Y-%m-%d'), '|', CAST(n.UNITNVRESTORED AS CHAR))
            ORDER BY n.TRADINGDAY DESC, n.INSERTTIME DESC, n.UPDATETIME DESC, n.ID DESC
            SEPARATOR ','
        ),
        ',',
        1
    ) AS latest_pair
FROM tmp_fund016_pool fp
STRAIGHT_JOIN mf_fundnetvalueretrans n FORCE INDEX (IX_MF_FUNDNETVALUERETRANS)
  ON n.INNERCODE = fp.INNERCODE
WHERE n.UNITNVRESTORED IS NOT NULL
  AND n.UNITNVRESTORED > 0
  AND n.TRADINGDAY >= DATE('{HISTORY_START}')
  AND n.TRADINGDAY <= DATE('{HISTORY_END}')
  AND n.INSERTTIME < DATE('{AS_OF_NEXT_DAY}')
GROUP BY n.INNERCODE, YEAR(n.TRADINGDAY), MONTH(n.TRADINGDAY)
"""


BENCH_MONTH_END_SQL = f"""
SELECT
    b.INNERCODE,
    'bench' AS series_type,
    DATE_FORMAT(d.trade_day, '%Y-%m') AS month_key,
    SUBSTRING_INDEX(
        GROUP_CONCAT(
            CONCAT(
                DATE_FORMAT(b.ENDDATE, '%Y-%m-%d'),
                '|',
                CAST(1.0 + b.BENCHGRFORSINCE / 100.0 AS CHAR)
            )
            ORDER BY b.INSERTTIME DESC, b.UPDATATIME DESC, b.JSID DESC
            SEPARATOR ','
        ),
        ',',
        1
    ) AS latest_pair
FROM tmp_fund016_month_ends d
JOIN mf_benchmarkgrowthrate b
  ON b.INNERCODE = d.INNERCODE
 AND b.ENDDATE >= d.trade_day
 AND b.ENDDATE < DATE_ADD(d.trade_day, INTERVAL 1 DAY)
WHERE b.BENCHGRFORSINCE IS NOT NULL
  AND b.INSERTTIME < DATE('{AS_OF_NEXT_DAY}')
GROUP BY b.INNERCODE, d.trade_day
"""


def run_conn_df(conn, sql: str, timeout: int = 180) -> pd.DataFrame:
    return get_db_connector().execute_sql_to_dataframe_on_connection(
        conn,
        sql,
        max_rows=0,
        timeout=timeout,
    )


def stage_fund_pool(conn, pool_df: pd.DataFrame) -> None:
    records = [(int(code),) for code in pool_df["INNERCODE"]]
    with conn.cursor() as cursor:
        cursor.execute("DROP TEMPORARY TABLE IF EXISTS tmp_fund016_pool")
        cursor.execute("CREATE TEMPORARY TABLE tmp_fund016_pool (INNERCODE BIGINT PRIMARY KEY)")
        cursor.executemany("INSERT INTO tmp_fund016_pool (INNERCODE) VALUES (%s)", records)
        cursor.execute("SET SESSION group_concat_max_len = 1000000")


def fetch_month_end_series(conn, pool_df: pd.DataFrame) -> pd.DataFrame:
    stage_fund_pool(conn, pool_df)
    fund_df = run_conn_df(conn, FUND_MONTH_END_SQL, timeout=300)
    if fund_df.empty:
        return fund_df

    def decode_levels(frame: pd.DataFrame) -> pd.DataFrame:
        pair = frame["latest_pair"].astype(str).str.split("|", n=1, expand=True)
        frame = frame.copy()
        frame["trade_date"] = pd.to_datetime(pair[0], errors="coerce")
        frame["level_value"] = pd.to_numeric(pair[1], errors="coerce")
        return frame.drop(columns=["latest_pair"])

    fund_df = decode_levels(fund_df).dropna(subset=["trade_date", "level_value"])
    with conn.cursor() as cursor:
        cursor.execute("DROP TEMPORARY TABLE IF EXISTS tmp_fund016_month_ends")
        cursor.execute(
            "CREATE TEMPORARY TABLE tmp_fund016_month_ends "
            "(INNERCODE BIGINT NOT NULL, trade_day DATE NOT NULL, "
            "PRIMARY KEY (INNERCODE, trade_day))"
        )
        cursor.executemany(
            "INSERT INTO tmp_fund016_month_ends (INNERCODE, trade_day) VALUES (%s, %s)",
            [(int(row.INNERCODE), row.trade_date.date()) for row in fund_df.itertuples()],
        )
    bench_df = run_conn_df(conn, BENCH_MONTH_END_SQL, timeout=300)
    if bench_df.empty:
        return bench_df
    series_df = pd.concat([fund_df, decode_levels(bench_df)], ignore_index=True)
    return series_df.merge(pool_df, on="INNERCODE", how="inner")


def main() -> pd.DataFrame:
    connector = get_db_connector()
    with connector.connection() as conn:
        pool_df = run_conn_df(conn, FUND_POOL_SQL)
        if pool_df.empty:
            raise ValueError("未取到观察日有效的偏股型开放式主份额基金。")
        pool_df["INNERCODE"] = pd.to_numeric(pool_df["INNERCODE"], errors="coerce")
        pool_df = pool_df.dropna(subset=["INNERCODE", "fund_code", "fund_name"]).copy()
        pool_df["INNERCODE"] = pool_df["INNERCODE"].astype(int)
        pool_df = pool_df.drop_duplicates(subset=["INNERCODE"]).reset_index(drop=True)

        series_df = fetch_month_end_series(conn, pool_df)
    if series_df.empty:
        raise ValueError("未取到偏股型开放式基金及业绩基准月末序列。")

    series_df["INNERCODE"] = pd.to_numeric(series_df["INNERCODE"], errors="coerce")
    series_df["trade_date"] = pd.to_datetime(series_df["trade_date"], errors="coerce")
    series_df["month_key"] = pd.PeriodIndex(series_df["month_key"], freq="M")
    series_df["level_value"] = pd.to_numeric(series_df["level_value"], errors="coerce")
    series_df = series_df.dropna(
        subset=["INNERCODE", "fund_code", "fund_name", "series_type", "trade_date", "month_key", "level_value"]
    ).copy()
    series_df = series_df[series_df["level_value"] > 0].copy()
    series_df["INNERCODE"] = series_df["INNERCODE"].astype(int)

    fund_month_df = (
        series_df[series_df["series_type"] == "fund"]
        .rename(columns={"level_value": "fund_level"})
        .copy()
    )
    bench_month_df = (
        series_df[series_df["series_type"] == "bench"][["INNERCODE", "month_key", "level_value"]]
        .rename(columns={"level_value": "bench_level"})
        .copy()
    )

    month_panel_df = fund_month_df.merge(bench_month_df, on=["INNERCODE", "month_key"], how="inner")
    month_panel_df = month_panel_df[month_panel_df["month_key"].isin(LEVEL_MONTHS)].copy()

    valid_innercodes = (
        month_panel_df.groupby("INNERCODE")["month_key"]
        .nunique()
        .loc[lambda s: s == len(LEVEL_MONTHS)]
        .index
    )
    month_panel_df = month_panel_df[month_panel_df["INNERCODE"].isin(valid_innercodes)].copy()
    if month_panel_df.empty:
        raise ValueError("没有同时具备完整13个月基金与基准月末水平的基金。")

    month_panel_df = month_panel_df.sort_values(["INNERCODE", "month_key"], kind="stable")
    month_panel_df["fund_month_return"] = month_panel_df.groupby("INNERCODE")["fund_level"].pct_change()
    month_panel_df["bench_month_return"] = month_panel_df.groupby("INNERCODE")["bench_level"].pct_change()

    return_panel_df = month_panel_df[month_panel_df["month_key"].isin(RETURN_MONTHS)].copy()
    return_panel_df["beat_flag"] = (
        return_panel_df["fund_month_return"] > return_panel_df["bench_month_return"]
    ).astype(int)

    stat_df = (
        return_panel_df.groupby(["INNERCODE", "fund_code", "fund_name"], as_index=False)
        .agg(
            total_months=("month_key", "nunique"),
            win_months=("beat_flag", "sum"),
        )
        .copy()
    )
    stat_df = stat_df[
        (stat_df["total_months"] == len(RETURN_MONTHS))
        & (stat_df["win_months"] >= MIN_WIN_MONTHS)
    ].copy()
    stat_df["win_rate"] = stat_df["win_months"] / stat_df["total_months"]

    one_year_df = (
        month_panel_df.groupby(["INNERCODE", "fund_code", "fund_name"], as_index=False)
        .agg(
            fund_start_level=("fund_level", "first"),
            fund_end_level=("fund_level", "last"),
            bench_start_level=("bench_level", "first"),
            bench_end_level=("bench_level", "last"),
        )
        .copy()
    )
    one_year_df["fund_return_1y"] = one_year_df["fund_end_level"] / one_year_df["fund_start_level"] - 1.0
    one_year_df["bench_return_1y"] = one_year_df["bench_end_level"] / one_year_df["bench_start_level"] - 1.0

    result_df = stat_df.merge(
        one_year_df[["INNERCODE", "fund_return_1y", "bench_return_1y"]],
        on="INNERCODE",
        how="inner",
    )

    if result_df.empty:
        raise ValueError(f"没有满足至少 {MIN_WIN_MONTHS} 个月跑赢自身业绩基准的基金。")

    output_df = result_df[
        ["fund_code", "fund_name", "win_months", "win_rate", "fund_return_1y", "bench_return_1y"]
    ].copy()
    output_df.columns = [
        "基金代码",
        "基金简称",
        "胜出月数",
        "胜率",
        "近 1 年基金收益",
        "近 1 年基准收益",
    ]

    output_df["胜出月数"] = output_df["胜出月数"].astype(int)
    output_df["胜率"] = (output_df["胜率"] * 100).round(2)
    output_df["近 1 年基金收益"] = (output_df["近 1 年基金收益"] * 100).round(2)
    output_df["近 1 年基准收益"] = (output_df["近 1 年基准收益"] * 100).round(2)

    output_df = output_df.sort_values(
        ["胜率", "基金代码"],
        ascending=[False, True],
    ).reset_index(drop=True)

    print(output_df.to_string(index=False))
    return output_df



if __name__ == "__main__":
    main().to_csv(Path(__file__).with_name("result.csv"), index=False, encoding="utf-8-sig")
