#!/usr/bin/env python3
from __future__ import annotations

import sys
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pandas as pd
import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")
except Exception:
    pass

import numpy as np
import pandas as pd
try:
    from sweagent.text2sql.db_connector import query_to_dataframe
except ImportError:
    from sweagent.text2sql.db_connector import query_to_dataframe

ANCHOR_DAY = pd.Timestamp("2025-07-31")
AS_OF_NEXT_DAY = pd.Timestamp("2025-08-01")
MANAGER_NAME = "谢治宇"
BULL_THRESHOLD = 0.03
BEAR_THRESHOLD = -0.03

RETURN_START_MONTH = pd.Period("2023-08", freq="M")
RETURN_END_MONTH = pd.Period("2025-07", freq="M")
ENDPOINT_START_MONTH = RETURN_START_MONTH - 1
window_start = ENDPOINT_START_MONTH.to_timestamp()
window_end = RETURN_END_MONTH.to_timestamp(how="end").floor("D")

CURRENT_FUND_SQL = f"""
WITH params AS (
    SELECT
        DATE('{ANCHOR_DAY:%Y-%m-%d}') AS anchor_day,
        DATE('{AS_OF_NEXT_DAY:%Y-%m-%d}') AS as_of_next_day
),
manager_current AS (
    SELECT
        fm.INNERCODE,
        DATE(fm.ACCESSIONDATE) AS accession_date,
        COALESCE(DATE(fm.DIMISSIONDATE), DATE('2099-12-31')) AS dimission_date
    FROM mf_fundmanagernew fm
    JOIN params p
      ON 1 = 1
    WHERE fm.NAME = '{MANAGER_NAME}'
      AND fm.POSTNAME = 1
      AND fm.ACCESSIONDATE IS NOT NULL
      AND DATE(fm.ACCESSIONDATE) <= p.anchor_day
      AND (fm.DIMISSIONDATE IS NULL OR DATE(fm.DIMISSIONDATE) > p.anchor_day)
      AND (fm.INFOPUBLDATE IS NULL OR DATE(fm.INFOPUBLDATE) < p.as_of_next_day)
),
fund_rank AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        COALESCE(sm.CHINAMEABBR, sm.SECUABBR, sm.CHINAME) AS fund_name,
        mc.accession_date,
        mc.dimission_date,
        ROW_NUMBER() OVER (
            PARTITION BY COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE)
            ORDER BY
                CASE
                    WHEN fa.SECURITYCODE = COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) THEN 1
                    ELSE 0
                END DESC,
                mc.accession_date ASC,
                fa.SECURITYCODE ASC
        ) AS rn
    FROM manager_current mc
    JOIN mf_fundarchives fa
      ON fa.INNERCODE = mc.INNERCODE
    JOIN secumain sm
      ON sm.INNERCODE = mc.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN params p
      ON 1 = 1
    WHERE COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.anchor_day)
      AND (fa.INSERTTIME IS NULL OR fa.INSERTTIME < p.as_of_next_day)
      AND sm.XGRQ < p.as_of_next_day
)
SELECT
    INNERCODE,
    fund_code,
    fund_name,
    accession_date,
    dimission_date
FROM fund_rank
WHERE rn = 1
ORDER BY fund_code
"""

HS300_SQL = f"""
WITH params AS (
    SELECT DATE('{AS_OF_NEXT_DAY:%Y-%m-%d}') AS as_of_next_day
),
hs300 AS (
    SELECT sm.INNERCODE
    FROM secumain sm
    JOIN params p
      ON 1 = 1
    WHERE sm.SECUCATEGORY = 4
      AND sm.SECUCODE = '000300'
      AND sm.XGRQ < p.as_of_next_day
    ORDER BY sm.INNERCODE
    LIMIT 1
)
SELECT
    q.ID,
    DATE(q.TRADINGDAY) AS trade_date,
    q.CLOSEPRICE AS close_price,
    q.XGRQ
FROM qt_indexquote q
JOIN hs300 h
  ON h.INNERCODE = q.INNERCODE
JOIN params p
  ON 1 = 1
WHERE q.TRADINGDAY >= DATE('{window_start:%Y-%m-%d}')
  AND q.TRADINGDAY <= DATE('{window_end:%Y-%m-%d}')
  AND q.CLOSEPRICE IS NOT NULL
  AND q.CLOSEPRICE > 0
  AND q.XGRQ < p.as_of_next_day
ORDER BY q.TRADINGDAY, q.XGRQ, q.ID
"""


def build_nav_sql(fund_ids: str) -> str:
    return f"""
    WITH params AS (
        SELECT DATE('{AS_OF_NEXT_DAY:%Y-%m-%d}') AS as_of_next_day
    ),
    nav_ranked AS (
        SELECT
            n.INNERCODE,
            DATE(n.TRADINGDAY) AS trade_date,
            n.UNITNVRESTORED AS adj_nav,
            ROW_NUMBER() OVER (
                PARTITION BY n.INNERCODE, DATE(n.TRADINGDAY)
                ORDER BY n.INSERTTIME DESC, n.UPDATETIME DESC, n.ID DESC
            ) AS rn
        FROM mf_fundnetvalueretrans n
        JOIN params p
          ON 1 = 1
        WHERE n.INNERCODE IN ({fund_ids})
          AND n.TRADINGDAY >= DATE('{window_start:%Y-%m-%d}')
          AND n.TRADINGDAY <= DATE('{window_end:%Y-%m-%d}')
          AND n.UNITNVRESTORED IS NOT NULL
          AND n.UNITNVRESTORED > 0
          AND n.INSERTTIME < p.as_of_next_day
          AND n.UPDATETIME < p.as_of_next_day
    )
    SELECT INNERCODE, trade_date, adj_nav
    FROM nav_ranked
    WHERE rn = 1
    ORDER BY INNERCODE, trade_date
    """


def calc_index_monthly_return(index_df: pd.DataFrame) -> pd.DataFrame:
    month_df = index_df.copy()
    month_df["year_month"] = month_df["trade_date"].dt.to_period("M")
    month_df = (
        month_df.groupby("year_month", as_index=False)
        .agg(
            month_end=("trade_date", "max"),
            end_value=("close_price", "last"),
        )
        .copy()
    )
    month_df = month_df.sort_values("year_month", kind="stable")
    month_df["hs300_monthly_ret"] = month_df["end_value"] / month_df["end_value"].shift(1) - 1.0
    month_df = month_df[
        (month_df["year_month"] >= RETURN_START_MONTH) &
        (month_df["year_month"] <= RETURN_END_MONTH)
    ].copy()
    month_df["market_state"] = np.select(
        [month_df["hs300_monthly_ret"] > BULL_THRESHOLD, month_df["hs300_monthly_ret"] < BEAR_THRESHOLD],
        ["牛市", "熊市"],
        default="震荡市",
    )
    return month_df


def calc_fund_monthly_return(nav_df: pd.DataFrame) -> pd.DataFrame:
    month_df = nav_df.copy()
    month_df["year_month"] = month_df["trade_date"].dt.to_period("M")
    month_df = (
        month_df.groupby(["INNERCODE", "year_month"], as_index=False)
        .agg(
            month_end=("trade_date", "max"),
            end_value=("adj_nav", "last"),
        )
        .copy()
    )
    month_df = month_df.sort_values(["INNERCODE", "year_month"], kind="stable")
    month_df["fund_monthly_ret"] = month_df.groupby("INNERCODE")["end_value"].pct_change()
    month_df = month_df[
        (month_df["year_month"] >= RETURN_START_MONTH) &
        (month_df["year_month"] <= RETURN_END_MONTH)
    ].copy()
    return month_df


def main() -> pd.DataFrame:
    fund_pool_df = query_to_dataframe(CURRENT_FUND_SQL, max_rows=0, timeout=120)
    if fund_pool_df.empty:
        raise ValueError("未找到谢治宇在兴证全球当前在管的主基金。")

    fund_pool_df["INNERCODE"] = pd.to_numeric(fund_pool_df["INNERCODE"], errors="coerce")
    fund_pool_df["accession_date"] = pd.to_datetime(fund_pool_df["accession_date"], errors="coerce")
    fund_pool_df["dimission_date"] = pd.to_datetime(fund_pool_df["dimission_date"], errors="coerce")
    fund_pool_df = fund_pool_df.dropna(
        subset=["INNERCODE", "fund_code", "fund_name", "accession_date", "dimission_date"]
    ).copy()
    fund_pool_df["INNERCODE"] = fund_pool_df["INNERCODE"].astype(int)

    fund_ids = ",".join(str(x) for x in fund_pool_df["INNERCODE"].tolist())

    nav_df = query_to_dataframe(build_nav_sql(fund_ids), max_rows=0, timeout=240)
    hs300_df = query_to_dataframe(HS300_SQL, max_rows=0, timeout=120)

    if nav_df.empty:
        raise ValueError("基金复权净值区间数据为空。")
    if hs300_df.empty:
        raise ValueError("沪深300区间行情为空。")

    nav_df["INNERCODE"] = pd.to_numeric(nav_df["INNERCODE"], errors="coerce")
    nav_df["trade_date"] = pd.to_datetime(nav_df["trade_date"], errors="coerce")
    nav_df["adj_nav"] = pd.to_numeric(nav_df["adj_nav"], errors="coerce")
    nav_df = nav_df.dropna(subset=["INNERCODE", "trade_date", "adj_nav"]).copy()
    nav_df["INNERCODE"] = nav_df["INNERCODE"].astype(int)

    hs300_df["trade_date"] = pd.to_datetime(hs300_df["trade_date"], errors="coerce")
    hs300_df["close_price"] = pd.to_numeric(hs300_df["close_price"], errors="coerce")
    hs300_df["XGRQ"] = pd.to_datetime(hs300_df["XGRQ"], errors="coerce")
    hs300_df["ID"] = pd.to_numeric(hs300_df["ID"], errors="coerce")
    hs300_df = hs300_df.dropna(subset=["trade_date", "close_price"]).copy()
    hs300_df = hs300_df.sort_values(["trade_date", "XGRQ", "ID"], kind="stable")
    hs300_df = hs300_df.drop_duplicates(subset=["trade_date"], keep="last")

    hs300_month_df = calc_index_monthly_return(hs300_df)
    fund_month_df = calc_fund_monthly_return(nav_df)
    fund_month_df = fund_month_df.merge(fund_pool_df, on="INNERCODE", how="inner")

    merged_df = fund_month_df.merge(
        hs300_month_df[["year_month", "hs300_monthly_ret", "market_state"]],
        on="year_month",
        how="inner",
    )
    merged_df["excess_ret"] = merged_df["fund_monthly_ret"] - merged_df["hs300_monthly_ret"]

    result_df = (
        merged_df.groupby(["fund_code", "fund_name", "market_state"], as_index=False)
        .agg(
            month_count=("excess_ret", "count"),
            avg_excess_ret=("excess_ret", "mean"),
        )
        .copy()
    )
    result_df = result_df[result_df["month_count"] > 0].copy()

    state_order = {"牛市": 1, "熊市": 2, "震荡市": 3}
    result_df["state_order"] = result_df["market_state"].map(state_order)
    result_df["avg_excess_ret"] = (result_df["avg_excess_ret"] * 100).round(2)

    output_df = (
        result_df.sort_values(["fund_code", "state_order"], kind="stable")
        [["fund_code", "fund_name", "market_state", "month_count", "avg_excess_ret"]]
        .rename(
            columns={
                "fund_code": "基金代码",
                "fund_name": "基金简称",
                "market_state": "市场状态",
                "month_count": "有效月份数",
                "avg_excess_ret": "平均月度超额收益",
            }
        )
        .reset_index(drop=True)
    )

    print(output_df.to_string(index=False))
    return output_df


if globals().get("__name__", "__main__") == "__main__":
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
                ("similarity", 120),
                ("result", 110),
                ("output", 100),
                ("fund_summary", 95),
                ("summary", 90),
                ("final", 80),
                ("candidate", 70),
                ("warning", 70),
                ("ratio", 60),
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
    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][2]


def __finalize_picture():
    png_files = sorted(
        p for p in Path.cwd().glob("*.png")
        if p.name != "picture.png"
    )
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


def __cleanup_extra_csv():
    for csv_path in Path.cwd().glob("*.csv"):
        if csv_path.name != "result.csv":
            csv_path.unlink(missing_ok=True)


def __write_result_csv():
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
    result_df.to_csv("result.csv", index=False, encoding="utf-8-sig", float_format="%.2f")
    __cleanup_extra_csv()
    __finalize_picture()


if globals().get("__name__", "__main__") == "__main__":
    __write_result_csv()
