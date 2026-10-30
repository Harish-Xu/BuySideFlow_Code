#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import get_db_connector


AS_OF_DATE = "2025-02-02"
AS_OF_NEXT_DAY = "2025-02-03"
START_2023 = "2023-01-01"
END_2023 = "2023-12-31"
START_2024 = "2024-01-01"
END_2024 = "2024-12-31"
INTERVAL_START = START_2023
INTERVAL_END = END_2024
CSI800_CODE = "000906"

YEAR_WINDOWS = {
    "2023 年超额收益": (START_2023, END_2023),
    "2024 年超额收益": (START_2024, END_2024),
}

RESULT_COLUMNS = [
    "基金代码",
    "基金简称",
    "2023 年超额收益",
    "2024 年超额收益",
    "基金区间最大回撤",
    "中证 800 区间最大回撤",
]


FUND_POOL_SQL = f"""
WITH params AS (
    SELECT
        DATE('{START_2023}') AS start_2023,
        DATE('{END_2024}') AS end_2024,
        DATE('{AS_OF_DATE}') AS as_of_date,
        DATE('{AS_OF_NEXT_DAY}') AS as_of_next_day
),
candidate_funds AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        sm.SECUABBR AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN params p
      ON 1 = 1
    WHERE fa.TYPE IN (2, 3, 7)
      AND fa.INVESTMENTTYPE = 16
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE < p.start_2023
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.as_of_date)
),
type_ranked AS (
    SELECT
        t.INNERCODE,
        t.SECASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS rn
    FROM mf_jyfundtype t
    JOIN candidate_funds cf
      ON cf.INNERCODE = t.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= p.as_of_date
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.as_of_date)
      AND t.INSERTTIME < p.as_of_next_day
),
fund_pool AS (
    SELECT
        cf.INNERCODE,
        cf.fund_code,
        cf.fund_name
    FROM candidate_funds cf
    JOIN type_ranked tr
      ON tr.INNERCODE = cf.INNERCODE
     AND tr.rn = 1
    WHERE tr.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
)
SELECT
    INNERCODE,
    fund_code,
    fund_name
FROM fund_pool
ORDER BY fund_code
"""


NAV_BATCH_SQL_TEMPLATE = """
WITH nav_ranked AS (
SELECT
    n.INNERCODE,
    DATE(n.TRADINGDAY) AS trade_date,
    n.UNITNVRESTORED AS adj_nav,
    ROW_NUMBER() OVER (
        PARTITION BY n.INNERCODE, DATE(n.TRADINGDAY)
        ORDER BY n.INSERTTIME DESC, n.UPDATETIME DESC, n.ID DESC
    ) AS rn
FROM mf_fundnetvalueretrans n FORCE INDEX (IX_MF_FUNDNETVALUERETRANS)
WHERE n.UNITNVRESTORED IS NOT NULL
  AND n.UNITNVRESTORED > 0
  AND n.INNERCODE IN ({innercodes})
  AND n.TRADINGDAY >= DATE('{start_date}')
  AND n.TRADINGDAY <= DATE('{end_date}')
  AND n.INSERTTIME < DATE('{as_of_next_day}')
), drawdown_path AS (
    SELECT
        INNERCODE,
        trade_date,
        adj_nav,
        MAX(adj_nav) OVER (
            PARTITION BY INNERCODE
            ORDER BY trade_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS running_peak
    FROM nav_ranked
    WHERE rn = 1
)
SELECT
    INNERCODE,
    MIN(adj_nav / NULLIF(running_peak, 0) - 1.0) * 100.0 AS fund_mdd_pct
FROM drawdown_path
GROUP BY INNERCODE
"""


ENDPOINT_SQL = f"""
WITH boundaries AS (
    SELECT 2023 AS nav_year, DATE('2022-12-31') AS start_boundary, DATE('2023-12-31') AS end_boundary
    UNION ALL
    SELECT 2024, DATE('2023-12-31'), DATE('2024-12-31')
),
endpoint_candidates AS (
    SELECT
        n.INNERCODE,
        b.nav_year,
        pt.point_type,
        DATE(n.TRADINGDAY) AS trade_date,
        n.UNITNVRESTORED AS adj_nav,
        ROW_NUMBER() OVER (
            PARTITION BY n.INNERCODE, b.nav_year, pt.point_type
            ORDER BY n.TRADINGDAY DESC, n.INSERTTIME DESC, n.UPDATETIME DESC, n.ID DESC
        ) AS rn
    FROM tmp_fund014_pool fp
    CROSS JOIN boundaries b
    CROSS JOIN (
        SELECT 'start' AS point_type
        UNION ALL
        SELECT 'end'
    ) pt
    JOIN mf_fundnetvalueretrans n FORCE INDEX (IX_MF_FUNDNETVALUERETRANS)
      ON n.INNERCODE = fp.INNERCODE
    JOIN tmp_fund014_csi_dates d
      ON d.trade_date = DATE(n.TRADINGDAY)
    WHERE n.UNITNVRESTORED IS NOT NULL
      AND n.UNITNVRESTORED > 0
      AND DATE(n.TRADINGDAY) <= CASE
          WHEN pt.point_type = 'start' THEN b.start_boundary
          ELSE b.end_boundary
      END
      AND n.INSERTTIME < DATE('{AS_OF_NEXT_DAY}')
)
SELECT
    INNERCODE,
    nav_year,
    MAX(
        CASE WHEN point_type = 'start' AND rn = 1
             THEN CONCAT(DATE_FORMAT(trade_date, '%Y-%m-%d'), '|', CAST(adj_nav AS CHAR))
        END
    ) AS first_pair,
    MAX(
        CASE WHEN point_type = 'end' AND rn = 1
             THEN CONCAT(DATE_FORMAT(trade_date, '%Y-%m-%d'), '|', CAST(adj_nav AS CHAR))
        END
    ) AS last_pair
FROM endpoint_candidates
WHERE rn = 1
GROUP BY INNERCODE, nav_year
HAVING first_pair IS NOT NULL AND last_pair IS NOT NULL
"""


CSI800_SQL = f"""
SELECT
    DATE(q.TRADINGDAY) AS trade_date,
    q.CLOSEPRICE AS csi800_close
FROM qt_indexquote q
JOIN secumain sm
  ON sm.INNERCODE = q.INNERCODE
WHERE sm.SECUCATEGORY = 4
  AND sm.SECUCODE = '{CSI800_CODE}'
  AND q.CLOSEPRICE IS NOT NULL
  AND q.CLOSEPRICE > 0
  AND q.TRADINGDAY <= DATE('{END_2024}')
ORDER BY q.TRADINGDAY
"""


def run_df(sql: str, timeout: int = 900) -> pd.DataFrame:
    connector = get_db_connector()
    return connector.execute_sql_to_dataframe(sql, max_rows=0, timeout=timeout)


def run_conn_df(conn, sql: str, timeout: int = 900) -> pd.DataFrame:
    return get_db_connector().execute_sql_to_dataframe_on_connection(conn, sql, max_rows=0, timeout=timeout)


def fetch_fund_pool(conn) -> pd.DataFrame:
    pool_df = run_conn_df(conn, FUND_POOL_SQL, timeout=300)
    if pool_df.empty:
        raise ValueError("未取到观察日有效的开放式主动权益主份额基金样本。")

    pool_df["INNERCODE"] = pd.to_numeric(pool_df["INNERCODE"], errors="coerce")
    pool_df = pool_df.dropna(subset=["INNERCODE", "fund_code", "fund_name"]).copy()
    pool_df["INNERCODE"] = pool_df["INNERCODE"].astype(int)
    return pool_df.drop_duplicates(subset=["INNERCODE"]).reset_index(drop=True)


def stage_fund_pool(conn, pool_df: pd.DataFrame) -> None:
    records = [
        (int(row.INNERCODE), str(row.fund_code), str(row.fund_name))
        for row in pool_df.itertuples(index=False)
    ]
    with conn.cursor() as cursor:
        cursor.execute("DROP TEMPORARY TABLE IF EXISTS tmp_fund014_pool")
        cursor.execute(
            """
            CREATE TEMPORARY TABLE tmp_fund014_pool (
                INNERCODE BIGINT PRIMARY KEY,
                fund_code VARCHAR(20),
                fund_name VARCHAR(100)
            )
            """
        )
        cursor.executemany(
            "INSERT INTO tmp_fund014_pool (INNERCODE, fund_code, fund_name) VALUES (%s, %s, %s)",
            records,
        )


def stage_csi_dates(conn, csi800_series: pd.Series) -> None:
    records = [(pd.Timestamp(date).strftime("%Y-%m-%d"),) for date in csi800_series.index]
    with conn.cursor() as cursor:
        cursor.execute("DROP TEMPORARY TABLE IF EXISTS tmp_fund014_csi_dates")
        cursor.execute("CREATE TEMPORARY TABLE tmp_fund014_csi_dates (trade_date DATE PRIMARY KEY)")
        cursor.executemany("INSERT INTO tmp_fund014_csi_dates (trade_date) VALUES (%s)", records)


def fetch_year_endpoints(
    conn,
    pool_df: pd.DataFrame,
    csi800_series: pd.Series,
) -> pd.DataFrame:
    if pool_df.empty or csi800_series.empty:
        raise ValueError("端点预筛缺少基金样本或中证 800 日期。")
    stage_fund_pool(conn, pool_df)
    stage_csi_dates(conn, csi800_series)
    endpoint_df = run_conn_df(conn, ENDPOINT_SQL, timeout=300)
    if endpoint_df.empty:
        raise ValueError("未取到样本基金 2023/2024 年首尾复权净值。")

    endpoint_df["INNERCODE"] = pd.to_numeric(endpoint_df["INNERCODE"], errors="coerce")
    endpoint_df["nav_year"] = pd.to_numeric(endpoint_df["nav_year"], errors="coerce")
    endpoint_df = endpoint_df.dropna(subset=["INNERCODE", "nav_year", "first_pair", "last_pair"]).copy()
    endpoint_df["INNERCODE"] = endpoint_df["INNERCODE"].astype(int)
    endpoint_df["nav_year"] = endpoint_df["nav_year"].astype(int)

    for side in ["first", "last"]:
        split_cols = endpoint_df[f"{side}_pair"].astype(str).str.split("|", n=1, expand=True)
        endpoint_df[f"{side}_date"] = pd.to_datetime(split_cols[0], errors="coerce")
        endpoint_df[f"{side}_nav"] = pd.to_numeric(split_cols[1], errors="coerce")

    endpoint_df = endpoint_df.dropna(subset=["first_date", "first_nav", "last_date", "last_nav"]).copy()
    return endpoint_df[["INNERCODE", "nav_year", "first_date", "first_nav", "last_date", "last_nav"]]


def build_year_excess_table(endpoint_df: pd.DataFrame, csi800_series: pd.Series) -> pd.DataFrame:
    rows_by_fund = {innercode: group for innercode, group in endpoint_df.groupby("INNERCODE", sort=False)}
    rows = []
    for innercode, group in rows_by_fund.items():
        result_row = {"INNERCODE": int(innercode)}
        for year in [2023, 2024]:
            one = group[group["nav_year"] == year]
            if one.empty:
                result_row = None
                break
            row = one.iloc[0]
            first_date = row["first_date"]
            last_date = row["last_date"]
            if first_date not in csi800_series.index or last_date not in csi800_series.index:
                result_row = None
                break
            fund_return = row["last_nav"] / row["first_nav"] - 1.0
            index_return = csi800_series.loc[last_date] / csi800_series.loc[first_date] - 1.0
            excess_return = float(fund_return - index_return)
            if excess_return <= 0:
                result_row = None
                break
            result_row[f"{year} 年超额收益"] = excess_return * 100.0
        if result_row is not None:
            rows.append(result_row)
    return pd.DataFrame(rows)


def fetch_fund_drawdown(conn, pool_df: pd.DataFrame, batch_size: int = 250) -> pd.DataFrame:
    frames = []
    innercodes = pool_df["INNERCODE"].astype(int).tolist()
    for start in range(0, len(innercodes), batch_size):
        batch = innercodes[start : start + batch_size]
        sql = NAV_BATCH_SQL_TEMPLATE.format(
            innercodes=",".join(str(code) for code in batch),
            start_date=START_2023,
            end_date=END_2024,
            as_of_next_day=AS_OF_NEXT_DAY,
        )
        batch_df = run_conn_df(conn, sql, timeout=300)
        if not batch_df.empty:
            frames.append(batch_df)

    drawdown_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if drawdown_df.empty:
        raise ValueError("未取到观察日有效主动权益基金的 2023-2024 复权净值。")

    drawdown_df["INNERCODE"] = pd.to_numeric(drawdown_df["INNERCODE"], errors="coerce")
    drawdown_df["fund_mdd_pct"] = pd.to_numeric(drawdown_df["fund_mdd_pct"], errors="coerce")
    drawdown_df = drawdown_df.dropna(subset=["INNERCODE", "fund_mdd_pct"]).copy()
    drawdown_df["INNERCODE"] = drawdown_df["INNERCODE"].astype(int)
    return drawdown_df


def fetch_csi800() -> pd.Series:
    index_df = run_df(CSI800_SQL, timeout=300)
    if index_df.empty:
        raise ValueError("未取到中证 800 在 2023-2024 的收盘价。")

    index_df["trade_date"] = pd.to_datetime(index_df["trade_date"], errors="coerce")
    index_df["csi800_close"] = pd.to_numeric(index_df["csi800_close"], errors="coerce")
    index_df = index_df.dropna(subset=["trade_date", "csi800_close"]).copy()
    index_df = index_df.sort_values("trade_date", kind="stable")
    index_df = index_df.drop_duplicates(subset=["trade_date"], keep="last")
    return index_df.set_index("trade_date")["csi800_close"].sort_index()


def calc_max_drawdown_pct(level_series: pd.Series) -> float:
    level_series = level_series.dropna().astype(float)
    if len(level_series) < 2 or (level_series <= 0).any():
        return np.nan

    levels = level_series.to_numpy(dtype=float)
    peaks = np.maximum.accumulate(levels)
    drawdowns = levels / peaks - 1.0
    return float(drawdowns.min() * 100.0)


def build_result(
    pool_df: pd.DataFrame,
    excess_df: pd.DataFrame,
    drawdown_df: pd.DataFrame,
    csi800_series: pd.Series,
) -> pd.DataFrame:
    csi800_mdd_pct = calc_max_drawdown_pct(csi800_series.loc[INTERVAL_START:INTERVAL_END])
    if np.isnan(csi800_mdd_pct):
        raise ValueError("中证 800 区间最大回撤不可计算。")

    result_df = (
        pool_df.merge(excess_df, on="INNERCODE", how="inner")
        .merge(drawdown_df, on="INNERCODE", how="inner")
    )
    result_df = result_df[result_df["fund_mdd_pct"] > csi800_mdd_pct].copy()
    if result_df.empty:
        raise ValueError("没有满足两年均跑赢中证800且区间最大回撤高于中证800的基金。")

    result_df = result_df.rename(
        columns={
            "fund_code": "基金代码",
            "fund_name": "基金简称",
            "fund_mdd_pct": "基金区间最大回撤",
        }
    )
    result_df["中证 800 区间最大回撤"] = csi800_mdd_pct
    result_df = result_df[RESULT_COLUMNS]

    result_df = result_df.sort_values(["基金区间最大回撤", "基金代码"], ascending=[False, True]).reset_index(drop=True)
    for column in RESULT_COLUMNS[2:]:
        result_df[column] = result_df[column].map(lambda value: f"{value:.2f}")
    return result_df


def main() -> pd.DataFrame:
    connector = get_db_connector()
    with connector.connection() as conn:
        pool_df = fetch_fund_pool(conn)
        csi800_series = fetch_csi800()
        endpoint_df = fetch_year_endpoints(conn, pool_df, csi800_series)
        excess_df = build_year_excess_table(endpoint_df, csi800_series)
        pool_df = pool_df[pool_df["INNERCODE"].isin(excess_df["INNERCODE"])].copy()
        if pool_df.empty:
            raise ValueError("没有满足 2023 和 2024 年均跑赢中证 800 的基金。")
        drawdown_df = fetch_fund_drawdown(conn, pool_df)
    result_df = build_result(pool_df, excess_df, drawdown_df, csi800_series)
    print(result_df.to_string(index=False))
    return result_df


if __name__ == "__main__":
    __result_obj = main()


def __is_scalar_like(value) -> bool:
    return isinstance(value, (str, int, float, bool, pd.Timestamp)) or value is None


def __normalize_result(value):
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if isinstance(value, pd.Series):
        return value.to_frame().T
    if isinstance(value, dict):
        if all(__is_scalar_like(v) for v in value.values()):
            return pd.DataFrame([value])
        frames = []
        for key, item in value.items():
            if isinstance(item, pd.DataFrame):
                tmp = item.copy()
                tmp.insert(0, "section", str(key))
                frames.append(tmp)
            elif isinstance(item, pd.Series):
                tmp = item.to_frame().T
                tmp.insert(0, "section", str(key))
                frames.append(tmp)
        if frames:
            return pd.concat(frames, ignore_index=True, sort=False)
        return None
    if isinstance(value, (list, tuple)):
        frames = []
        for idx, item in enumerate(value):
            if isinstance(item, pd.DataFrame):
                tmp = item.copy()
                tmp.insert(0, "section", f"part_{idx+1}")
                frames.append(tmp)
            elif isinstance(item, pd.Series):
                tmp = item.to_frame().T
                tmp.insert(0, "section", f"part_{idx+1}")
                frames.append(tmp)
        if frames:
            return pd.concat(frames, ignore_index=True, sort=False)
    return None


def __pick_global_frame(namespace: dict):
    candidates = []
    for name, value in namespace.items():
        if name.startswith("__"):
            continue
        if isinstance(value, pd.DataFrame):
            lname = name.lower()
            score = 0
            for keyword, weight in [
                ("result", 110),
                ("output", 100),
                ("summary", 90),
                ("final", 80),
                ("metric", 50),
                ("df", 10),
            ]:
                if keyword in lname:
                    score = max(score, weight)
            score += min(len(value.columns), 50)
            candidates.append((score, name, value.copy()))
        elif isinstance(value, pd.Series):
            candidates.append((40, name, value.to_frame().T))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2]


def __finalize_picture() -> None:
    png_files = sorted(p for p in Path.cwd().glob("*.png") if p.name != "picture.png")
    if not png_files:
        return
    picture_path = Path("picture.png")
    if len(png_files) == 1:
        shutil.copyfile(png_files[0], picture_path)
        if png_files[0] != picture_path:
            png_files[0].unlink(missing_ok=True)
        return
    try:
        from PIL import Image

        images = [Image.open(path).convert("RGB") for path in png_files]
        width = max(img.width for img in images)
        height = sum(img.height for img in images)
        canvas = Image.new("RGB", (width, height), color=(255, 255, 255))
        y = 0
        for img in images:
            canvas.paste(img, (0, y))
            y += img.height
            img.close()
        canvas.save(picture_path)
        for path in png_files:
            path.unlink(missing_ok=True)
    except Exception:
        shutil.copyfile(png_files[0], picture_path)
        for path in png_files:
            if path != picture_path:
                path.unlink(missing_ok=True)


def __cleanup_extra_csv() -> None:
    for csv_path in Path.cwd().glob("*.csv"):
        if csv_path.name != "result.csv":
            csv_path.unlink(missing_ok=True)


def __write_result_csv() -> None:
    result_obj = globals().get("__result_obj")
    result_df = __normalize_result(result_obj)
    if result_df is None:
        result_df = __pick_global_frame(globals())
    if result_df is None:
        raise ValueError("No DataFrame-like result found to write as result.csv")
    result_df = result_df.copy()
    for column in result_df.columns:
        if pd.api.types.is_datetime64_any_dtype(result_df[column]):
            result_df[column] = result_df[column].dt.strftime("%Y-%m-%d")
    result_df.to_csv("result.csv", index=False, encoding="utf-8-sig")
    __cleanup_extra_csv()
    __finalize_picture()


if __name__ == "__main__":
    __write_result_csv()
