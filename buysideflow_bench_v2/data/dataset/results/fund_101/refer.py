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

import json

import pandas as pd

try:
    from sweagent.text2sql.db_connector import query_to_dataframe
except ImportError:
    from sweagent.text2sql.db_connector import query_to_dataframe

START_DATE = "2024-12-31"
END_DATE = "2025-12-31"

sql = f"""
WITH params AS (
    SELECT DATE('{START_DATE}') AS start_date,
           DATE('{END_DATE}') AS end_date
),
latest_type AS (
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
      AND t.EFFECTIVEDATE <= p.end_date
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.end_date)
      AND t.INSERTTIME < DATE_ADD(p.end_date, INTERVAL 1 DAY)
),
fund_pool AS (
    SELECT
        fa.INNERCODE,
        sm.SECUCODE,
        sm.SECUABBR
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
    JOIN latest_type lt
      ON lt.INNERCODE = fa.INNERCODE
     AND lt.rn = 1
    JOIN params p
      ON 1 = 1
    WHERE sm.SECUCATEGORY = 8
      AND lt.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
      AND fa.INVESTMENTTYPE = 16
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= p.end_date
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > p.end_date)
),
nav_dedup AS (
    SELECT
        fp.INNERCODE,
        fp.SECUCODE,
        DATE(nv.TRADINGDAY) AS trading_day,
        nv.UNITNVRESTORED,
        ROW_NUMBER() OVER (
            PARTITION BY fp.INNERCODE, DATE(nv.TRADINGDAY)
            ORDER BY nv.INSERTTIME DESC, nv.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans nv
    JOIN fund_pool fp
      ON fp.INNERCODE = nv.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE DATE(nv.TRADINGDAY) IN (p.start_date, p.end_date)
),
nav_points AS (
    SELECT
        INNERCODE,
        SECUCODE,
        trading_day,
        UNITNVRESTORED
    FROM nav_dedup
    WHERE rn = 1
)
SELECT
    INNERCODE,
    SECUCODE,
    MAX(CASE WHEN trading_day = DATE('{START_DATE}') THEN UNITNVRESTORED END) AS start_nav,
    MAX(CASE WHEN trading_day = DATE('{END_DATE}') THEN UNITNVRESTORED END) AS end_nav,
    SUM(CASE WHEN trading_day = DATE('{START_DATE}') THEN 1 ELSE 0 END) AS has_start,
    SUM(CASE WHEN trading_day = DATE('{END_DATE}') THEN 1 ELSE 0 END) AS has_end
FROM nav_points
GROUP BY INNERCODE, SECUCODE
HAVING has_start = 1 AND has_end = 1
ORDER BY SECUCODE
"""

df = query_to_dataframe(sql, max_rows=0)
if df.empty:
    raise ValueError("未取到满足条件的主动权益基金样本。")

df["start_nav"] = pd.to_numeric(df["start_nav"], errors="coerce")
df["end_nav"] = pd.to_numeric(df["end_nav"], errors="coerce")
df = df.dropna(subset=["start_nav", "end_nav"]).copy()
df = df[df["start_nav"] > 0].copy()
if df.empty:
    raise ValueError("样本基金缺少有效起止净值，无法计算年度收益。")

df["return_pct"] = (df["end_nav"] / df["start_nav"] - 1.0) * 100.0
returns = df["return_pct"]

result = {
    "样本基金数量": int(len(df)),
    "平均收益率": round(float(returns.mean()), 2),
    "中位数": round(float(returns.median()), 2),
    "10%分位数": round(float(returns.quantile(0.10)), 2),
    "90%分位数": round(float(returns.quantile(0.90)), 2),
}
__result_obj = result

print(json.dumps(result, ensure_ascii=False, indent=2))


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
    result_df.to_csv("result.csv", index=False, encoding="utf-8-sig")
    __cleanup_extra_csv()
    __finalize_picture()


__write_result_csv()
