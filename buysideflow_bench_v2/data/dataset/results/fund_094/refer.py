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

import pandas as pd

try:
    from sweagent.text2sql.db_connector import query_to_dataframe
except ImportError:
    from sweagent.text2sql.db_connector import query_to_dataframe

ASOF_DATE = "2025-09-30"
LOOKBACK_YEARS = 3
OUTPUT_ORDER = ["014736.OF", "014232.OF", "000940.OF"]

sql = f"""
WITH params AS (
    SELECT
        DATE('{ASOF_DATE}') AS asof_date,
        DATE_SUB(DATE('{ASOF_DATE}'), INTERVAL {LOOKBACK_YEARS} YEAR) AS start_date
),
nav_dedup AS (
    SELECT
        s.SECUCODE,
        DATE(n.TRADINGDAY) AS trading_day,
        n.NVRDAILYGROWTHRATE,
        ROW_NUMBER() OVER (
            PARTITION BY s.SECUCODE, DATE(n.TRADINGDAY)
            ORDER BY n.TRADINGDAY DESC, n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans n
    JOIN secumain s
      ON s.INNERCODE = n.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE s.SECUCATEGORY = 8
      AND s.SECUCODE IN ('014736', '014232', '000940')
      AND n.TRADINGDAY >= p.start_date
      AND n.TRADINGDAY < DATE_ADD(p.asof_date, INTERVAL 1 DAY)
      AND n.INSERTTIME < DATE_ADD(p.asof_date, INTERVAL 1 DAY)
      AND n.NVRDAILYGROWTHRATE IS NOT NULL
      AND EXISTS (  -- 只用沪深交易日，排除季末、年末非交易日的补记净值
          SELECT 1 FROM qt_tradingdaynew t
          WHERE t.TRADINGDATE = DATE(n.TRADINGDAY) AND t.SECUMARKET = 83 AND t.IFTRADINGDAY = 1
      )
)
SELECT
    SECUCODE,
    trading_day,
    NVRDAILYGROWTHRATE
FROM nav_dedup
WHERE rn = 1
ORDER BY SECUCODE, trading_day
"""

df = query_to_dataframe(sql, max_rows=0)

if df.empty:
    raise ValueError("未取到收益率数据，请核查基金代码、日期区间和字段口径。")

df["trading_day"] = pd.to_datetime(df["trading_day"])
df["NVRDAILYGROWTHRATE"] = pd.to_numeric(df["NVRDAILYGROWTHRATE"], errors="coerce")
df = df.dropna(subset=["NVRDAILYGROWTHRATE"]).copy()
df["fund_code"] = df["SECUCODE"].astype(str) + ".OF"

aligned_ret = (
    df.pivot(index="trading_day", columns="fund_code", values="NVRDAILYGROWTHRATE")
      .reindex(columns=OUTPUT_ORDER)
      .dropna(how="any")
      .sort_index()
      / 100.0
)

if aligned_ret.empty:
    raise ValueError("共同交易日样本为空，无法计算协方差矩阵。")

cov_matrix = aligned_ret.cov().reindex(index=OUTPUT_ORDER, columns=OUTPUT_ORDER)
cov_matrix.index.name = "基金代码"
cov_matrix.columns.name = ""

print(f"aligned_trading_days={len(aligned_ret)}")
print(cov_matrix.to_string(float_format=lambda x: f"{x:.6f}"))

__result_obj = cov_matrix.reset_index()


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
    result_df.to_csv("result.csv", index=False, encoding="utf-8-sig", float_format="%.6f")
    __cleanup_extra_csv()
    __finalize_picture()


__write_result_csv()
