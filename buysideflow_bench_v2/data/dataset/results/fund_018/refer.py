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


ANCHOR_DAY = "2026-01-30"
AS_OF_NEXT_DAY = "2026-01-31"
TARGET_FUND_CODES = ("008269", "008134")


SQL = f"""
WITH params AS (
    SELECT
        DATE('{ANCHOR_DAY}') AS anchor_day,
        DATE('{AS_OF_NEXT_DAY}') AS as_of_next_day,
        DATE_SUB(DATE('{ANCHOR_DAY}'), INTERVAL 5 YEAR) AS lookback_start
),
target_funds AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        COALESCE(sm.CHINAMEABBR, sm.SECUABBR) AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN params p
      ON 1 = 1
    WHERE COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
      AND fa.SECURITYCODE IN ('{TARGET_FUND_CODES[0]}', '{TARGET_FUND_CODES[1]}')
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.anchor_day)
      AND (fa.INSERTTIME IS NULL OR fa.INSERTTIME < p.as_of_next_day)
      AND fa.XGRQ < p.as_of_next_day
      AND sm.XGRQ < p.as_of_next_day
),
nav_dedup AS (
    SELECT
        tf.INNERCODE,
        tf.fund_code,
        tf.fund_name,
        DATE(n.TRADINGDAY) AS trade_date,
        n.UNITNVRESTORED AS adj_nav,
        n.INSERTTIME,
        n.UPDATETIME,
        n.ID,
        ROW_NUMBER() OVER (
            PARTITION BY tf.INNERCODE, DATE(n.TRADINGDAY)
            ORDER BY n.INSERTTIME DESC, n.UPDATETIME DESC, n.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans n
    JOIN target_funds tf
      ON tf.INNERCODE = n.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE n.UNITNVRESTORED IS NOT NULL
      AND n.UNITNVRESTORED > 0
      AND n.TRADINGDAY >= p.lookback_start
      AND n.TRADINGDAY <= p.anchor_day
      AND n.INSERTTIME < p.as_of_next_day
      AND n.UPDATETIME < p.as_of_next_day
)
SELECT
    INNERCODE,
    fund_code,
    fund_name,
    trade_date,
    adj_nav
FROM nav_dedup
WHERE rn = 1
ORDER BY INNERCODE, trade_date
"""


def calc_recovery_days(level_df: pd.DataFrame) -> list[int]:
    return [event["recovery_days"] for event in calc_drawdown_events(level_df)["repaired_events"]]


def calc_drawdown_events(level_df: pd.DataFrame) -> dict:
    level_df = level_df.sort_values("trade_date", kind="stable").copy()
    level_df = level_df.dropna(subset=["trade_date", "adj_nav"])
    if level_df.empty:
        return {"repaired_events": [], "has_unrepaired_deep_drawdown": False}

    repaired_events: list[dict] = []
    has_unrepaired_deep_drawdown = False

    first_row = level_df.iloc[0]
    peak_nav = float(first_row["adj_nav"])
    peak_date = pd.Timestamp(first_row["trade_date"])
    in_drawdown = False
    valley_nav = peak_nav
    valley_date = peak_date
    min_drawdown = 0.0
    drawdown_start_date = None

    for _, row in level_df.iloc[1:].iterrows():
        current_date = pd.Timestamp(row["trade_date"])
        current_nav = float(row["adj_nav"])

        if not in_drawdown:
            if current_nav >= peak_nav:
                peak_nav = current_nav
                peak_date = current_date
                continue
            in_drawdown = True
            drawdown_start_date = current_date
            valley_nav = current_nav
            valley_date = current_date
            min_drawdown = current_nav / peak_nav - 1.0
            continue

        if current_nav < valley_nav:
            valley_nav = current_nav
            valley_date = current_date
            min_drawdown = current_nav / peak_nav - 1.0

        if current_nav >= peak_nav:
            if min_drawdown < -0.10:
                repaired_events.append(
                    {
                        "drawdown_start_date": drawdown_start_date,
                        "peak_date": peak_date,
                        "valley_date": valley_date,
                        "recovery_date": current_date,
                        "max_drawdown": -min_drawdown,
                        "recovery_days": int((current_date - valley_date).days),
                    }
                )
            in_drawdown = False
            peak_nav = current_nav
            peak_date = current_date
            valley_nav = current_nav
            valley_date = current_date
            min_drawdown = 0.0
            drawdown_start_date = None

    if in_drawdown and min_drawdown < -0.10:
        has_unrepaired_deep_drawdown = True

    return {
        "repaired_events": repaired_events,
        "has_unrepaired_deep_drawdown": has_unrepaired_deep_drawdown,
    }


def main() -> pd.DataFrame:
    nav_df = query_to_dataframe(SQL, max_rows=0, timeout=300)
    if nav_df.empty:
        raise ValueError("未取到目标基金近五年复权净值数据。")

    nav_df["INNERCODE"] = pd.to_numeric(nav_df["INNERCODE"], errors="coerce")
    nav_df["trade_date"] = pd.to_datetime(nav_df["trade_date"], errors="coerce")
    nav_df["adj_nav"] = pd.to_numeric(nav_df["adj_nav"], errors="coerce")
    nav_df = nav_df.dropna(subset=["INNERCODE", "fund_code", "fund_name", "trade_date", "adj_nav"]).copy()
    nav_df["INNERCODE"] = nav_df["INNERCODE"].astype(int)

    fund_meta_df = (
        nav_df[["INNERCODE", "fund_code", "fund_name"]]
        .drop_duplicates(subset=["INNERCODE"])
        .copy()
    )

    found_codes = set(fund_meta_df["fund_code"].tolist())
    missing_codes = [code for code in TARGET_FUND_CODES if code not in found_codes]
    if missing_codes:
        raise ValueError(f"未找到目标主基金代码: {', '.join(missing_codes)}")

    rows = []
    for innercode, group in nav_df.groupby("INNERCODE", sort=False):
        event_info = calc_drawdown_events(group)
        repaired_events = event_info["repaired_events"]
        recovery_days = [event["recovery_days"] for event in repaired_events]
        max_drawdowns = [event["max_drawdown"] for event in repaired_events]
        avg_days = float(np.mean(recovery_days)) if recovery_days else np.nan
        avg_max_drawdown = float(np.mean(max_drawdowns)) if max_drawdowns else np.nan
        fund_row = fund_meta_df.loc[fund_meta_df["INNERCODE"] == innercode].iloc[0]
        rows.append(
            {
                "基金代码": fund_row["fund_code"],
                "基金简称": fund_row["fund_name"],
                "已修复深度回撤次数": len(repaired_events),
                "平均最大回撤": avg_max_drawdown,
                "平均回撤修复天数": avg_days,
                "是否仍有未修复深度回撤": "是" if event_info["has_unrepaired_deep_drawdown"] else "否",
            }
        )

    output_df = pd.DataFrame(rows)
    output_df["平均最大回撤"] = (output_df["平均最大回撤"] * 100).round(2)
    output_df["平均回撤修复天数"] = output_df["平均回撤修复天数"].round(2)
    output_df = output_df.sort_values("基金代码", kind="stable").reset_index(drop=True)

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
    result_df.to_csv("result.csv", index=False, encoding="utf-8-sig")
    __cleanup_extra_csv()
    __finalize_picture()


__write_result_csv()
