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

from pathlib import Path
import sys

from sweagent.text2sql.db_connector import query_to_dataframe

ESTABLISHMENT_CUTOFF_DAY = '2025-09-30'
YEAR_START_DAY = '2026-01-01'
MONTH_START_DAY = '2026-02-01'
ANCHOR_DAY = '2026-02-28'

sql = f"""
WITH anchor_date AS (
    SELECT
        DATE('{ESTABLISHMENT_CUTOFF_DAY}') AS establishment_cutoff_day,
        DATE('{YEAR_START_DAY}') AS year_start_day,
        DATE('{MONTH_START_DAY}') AS month_start_day,
        DATE('{ANCHOR_DAY}') AS anchor_day
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
    JOIN anchor_date ad
      ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= ad.anchor_day
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > ad.anchor_day)
      AND t.INSERTTIME < DATE_ADD(ad.anchor_day, INTERVAL 1 DAY)
),
active_equity AS (
    SELECT
        f.INNERCODE,
        sm.SECUCODE,
        sm.SECUABBR
    FROM mf_fundarchives f
    JOIN type_asof ta
      ON ta.INNERCODE = f.INNERCODE
     AND ta.rn = 1
    JOIN secumain sm
      ON sm.INNERCODE = f.INNERCODE
    JOIN anchor_date ad
      ON 1 = 1
    WHERE ta.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
      AND f.INVESTMENTTYPE = 16
      AND f.TYPE IN (2, 3, 7, 8)
      AND f.FLOATTYPE IN (2, 3)
      AND f.MAINCODE = f.SECURITYCODE
      AND COALESCE(f.IFFOF, 2) <> 1
      AND f.ESTABLISHMENTDATE < ad.establishment_cutoff_day
      AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE > ad.anchor_day)
),
nav_dedup AS (
    SELECT
        n.INNERCODE,
        DATE(n.TRADINGDAY) AS TRADINGDAY,
        n.UNITNVRESTORED,
        ROW_NUMBER() OVER (
            PARTITION BY n.INNERCODE, DATE(n.TRADINGDAY)
            ORDER BY n.TRADINGDAY DESC, n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM active_equity ae
    JOIN mf_fundnetvalueretrans n
      ON n.INNERCODE = ae.INNERCODE
    JOIN anchor_date ad
      ON 1 = 1
    WHERE n.TRADINGDAY >= ad.year_start_day
      AND n.TRADINGDAY <= ad.anchor_day
      AND n.UNITNVRESTORED IS NOT NULL
      AND n.INSERTTIME < DATE_ADD(ad.anchor_day, INTERVAL 1 DAY)
),
nav_series AS (
    SELECT
        ae.INNERCODE,
        ae.SECUCODE,
        ae.SECUABBR,
        nd.TRADINGDAY,
        nd.UNITNVRESTORED
    FROM nav_dedup nd
    JOIN active_equity ae
      ON ae.INNERCODE = nd.INNERCODE
    WHERE nd.rn = 1
),
with_prev_peak AS (
    SELECT
        ns.*,
        MAX(ns.UNITNVRESTORED) OVER (
            PARTITION BY ns.INNERCODE
            ORDER BY ns.TRADINGDAY
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS prev_peak_ytd
    FROM nav_series ns
),
fund_high_counts AS (
    SELECT
        p.INNERCODE,
        p.SECUCODE AS fund_code,
        p.SECUABBR AS fund_name,
        CAST(SUM(CASE WHEN p.prev_peak_ytd IS NOT NULL AND p.UNITNVRESTORED > p.prev_peak_ytd THEN 1 ELSE 0 END) AS UNSIGNED) AS new_high_count
    FROM with_prev_peak p
    JOIN anchor_date ad
      ON 1 = 1
    WHERE p.TRADINGDAY >= ad.month_start_day
      AND p.TRADINGDAY <= ad.anchor_day
    GROUP BY p.INNERCODE, p.SECUCODE, p.SECUABBR
),
ranked_funds AS (
    SELECT
        f.*,
        RANK() OVER (ORDER BY f.new_high_count DESC) AS rk
    FROM fund_high_counts f
)
SELECT
    fund_code,
    fund_name,
    new_high_count
FROM ranked_funds
WHERE rk <= 20
ORDER BY new_high_count DESC, fund_code
"""

result = query_to_dataframe(sql, timeout=600)
result = result.rename(
    columns={
        "fund_code": "基金代码",
        "fund_name": "基金简称",
        "new_high_count": "创新高次数",
    }
)[["基金代码", "基金简称", "创新高次数"]]
print(result.to_string(index=False))


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
