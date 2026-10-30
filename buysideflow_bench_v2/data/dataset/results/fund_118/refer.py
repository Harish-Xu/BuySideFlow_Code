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

sql_query = """
WITH params AS (
    SELECT DATE('2026-01-30') AS anchor_date
),
main_fund_base AS (
    SELECT
        fa.INNERCODE,
        sm.SECUCODE AS fund_code,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME) AS fund_name,
        DATE(COALESCE(fa.ESTABLISHMENTDATE, fa.STARTDATE)) AS establishment_date
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN params p
      ON 1 = 1
    WHERE COALESCE(fa.IFFOF, 2) <> 1
      AND fa.TYPE IN (2, 3, 4, 7, 8)
      AND fa.MAINCODE IS NOT NULL
      AND CAST(fa.MAINCODE AS CHAR) <> ''
      AND LPAD(CAST(fa.MAINCODE AS CHAR), 6, '0') = LPAD(CAST(fa.SECURITYCODE AS CHAR), 6, '0')
      AND COALESCE(fa.ESTABLISHMENTDATE, fa.STARTDATE) <= DATE_SUB(p.anchor_date, INTERVAL 5 YEAR)
      AND (fa.EXPIREDATE IS NULL OR DATE(fa.EXPIREDATE) > p.anchor_date)
),
type_asof AS (
    SELECT
        jt.INNERCODE,
        jt.SECASSETCATCODE,
        jt.SECASSETCATNAME,
        jt.THIRDASSETCATCODE,
        jt.THIRDASSETCATNAME,
        ROW_NUMBER() OVER (
            PARTITION BY jt.INNERCODE
            ORDER BY jt.EFFECTIVEDATE DESC, jt.INSERTTIME DESC, jt.ID DESC
        ) AS rn
    FROM mf_jyfundtype jt
    JOIN main_fund_base mb
      ON mb.INNERCODE = jt.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE jt.STANDARD = 75
      AND jt.EFFECTIVEDATE <= p.anchor_date
      AND (jt.CANCELDATE IS NULL OR jt.CANCELDATE > p.anchor_date)
      AND jt.INSERTTIME < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
),
scale_raw AS (
    SELECT
        sa.INNERCODE,
        sa.ENDDATE AS scale_date,
        sa.NVI AS share_scale,
        ROW_NUMBER() OVER (
            PARTITION BY sa.INNERCODE
            ORDER BY sa.ENDDATE DESC, sa.INSERTTIME DESC, sa.ID DESC
        ) AS rn
    FROM mf_scaleanalysis sa
    JOIN type_asof ta
      ON ta.INNERCODE = sa.INNERCODE
     AND ta.rn = 1
    JOIN params p
      ON 1 = 1
    WHERE sa.TYPECODE = 10
      AND sa.ENDDATE <= p.anchor_date
      AND sa.NVI IS NOT NULL
      AND sa.INSERTTIME < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
),
manager_raw AS (
    SELECT
        m.INNERCODE,
        m.PERSONALCODE,
        m.NAME AS manager_name,
        DATE(m.ACCESSIONDATE) AS accession_date,
        ROW_NUMBER() OVER (
            PARTITION BY m.INNERCODE,
                         m.PERSONALCODE,
                         DATE(m.ACCESSIONDATE),
                         COALESCE(DATE(m.DIMISSIONDATE), DATE('9999-12-31'))
            ORDER BY m.ID DESC
        ) AS rn
    FROM mf_fundmanagernew m
    JOIN type_asof ta
      ON ta.INNERCODE = m.INNERCODE
     AND ta.rn = 1
    JOIN params p
      ON 1 = 1
    WHERE m.POSTNAME = 1
      AND m.ACCESSIONDATE IS NOT NULL
      AND DATE(m.ACCESSIONDATE) <= p.anchor_date
      AND (m.DIMISSIONDATE IS NULL OR DATE(m.DIMISSIONDATE) > p.anchor_date)
      AND m.INFOPUBLDATE < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
),
manager_qualified AS (
    SELECT
        mr.INNERCODE,
        mr.PERSONALCODE,
        mr.manager_name,
        mr.accession_date
    FROM manager_raw mr
    JOIN params p
      ON 1 = 1
    WHERE mr.rn = 1
      AND mr.accession_date <= DATE_SUB(p.anchor_date, INTERVAL 2 YEAR)
),
manager_list AS (
    SELECT
        INNERCODE,
        GROUP_CONCAT(manager_name ORDER BY accession_date, PERSONALCODE SEPARATOR ',') AS qualified_manager_names
    FROM manager_qualified
    GROUP BY INNERCODE
)
SELECT
    mb.INNERCODE,
    mb.fund_code,
    mb.fund_name,
    mb.establishment_date,
    ta.SECASSETCATCODE,
    ta.SECASSETCATNAME,
    ta.THIRDASSETCATCODE,
    ta.THIRDASSETCATNAME,
    COALESCE(ta.THIRDASSETCATNAME, ta.SECASSETCATNAME) AS effective_category_name,
    sr.scale_date,
    ROUND(sr.share_scale / 100000000, 4) AS share_scale_yi,
    ml.qualified_manager_names
FROM main_fund_base mb
JOIN type_asof ta
  ON ta.INNERCODE = mb.INNERCODE
 AND ta.rn = 1
JOIN scale_raw sr
  ON sr.INNERCODE = mb.INNERCODE
 AND sr.rn = 1
JOIN manager_list ml
  ON ml.INNERCODE = mb.INNERCODE
WHERE sr.share_scale >= 200000000
  AND ta.THIRDASSETCATCODE IN ('120201', '120401', '130202')
ORDER BY mb.INNERCODE
"""

import numpy as np
import pandas as pd

from sweagent.text2sql.db_connector import query_to_dataframe


ANCHOR_DATE = pd.Timestamp("2026-01-30")


def slice_window(nav_series: pd.Series, anchor_date: pd.Timestamp, years: int) -> pd.Series:
    target_start = anchor_date - pd.DateOffset(years=years)
    start_candidates = nav_series[nav_series.index <= target_start]
    end_candidates = nav_series[nav_series.index <= anchor_date]
    if start_candidates.empty or end_candidates.empty:
        return nav_series.iloc[0:0]
    start_date = start_candidates.index[-1]
    end_date = end_candidates.index[-1]
    if start_date >= end_date:
        return nav_series.iloc[0:0]
    return nav_series[(nav_series.index >= start_date) & (nav_series.index <= end_date)]


def annualized_return(nav_series: pd.Series) -> float:
    if len(nav_series) < 2:
        return np.nan
    start_nav = float(nav_series.iloc[0])
    end_nav = float(nav_series.iloc[-1])
    n_returns = len(nav_series) - 1
    if start_nav <= 0 or end_nav <= 0 or n_returns <= 0:
        return np.nan
    return (end_nav / start_nav) ** (250.0 / n_returns) - 1


def max_drawdown(nav_series: pd.Series) -> float:
    if len(nav_series) < 2:
        return np.nan
    rolling_peak = nav_series.cummax()
    drawdown = nav_series / rolling_peak - 1.0
    return float(drawdown.min())


def fetch_nav_panel(innercodes: list[int]) -> pd.DataFrame:
    if not innercodes:
        return pd.DataFrame(columns=["INNERCODE", "TRADINGDAY", "UNITNVRESTORED"])

    frames = []
    for start in range(0, len(innercodes), 50):
        batch = innercodes[start : start + 50]
        innercode_list = ",".join(str(int(code)) for code in batch)
        nav_sql = f"""
WITH params AS (
    SELECT DATE('2026-01-30') AS anchor_date
),
nav_ranked AS (
    SELECT
        n.INNERCODE,
        n.TRADINGDAY,
        n.UNITNVRESTORED,
        ROW_NUMBER() OVER (
            PARTITION BY n.INNERCODE, n.TRADINGDAY
            ORDER BY n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans n FORCE INDEX (IX_MF_FUNDNETVALUERETRANS)
    JOIN params p
      ON 1 = 1
    WHERE n.INNERCODE IN ({innercode_list})
      AND n.TRADINGDAY >= DATE_SUB(DATE_SUB(p.anchor_date, INTERVAL 5 YEAR), INTERVAL 10 DAY)
      AND n.TRADINGDAY <= p.anchor_date
      AND n.UNITNVRESTORED IS NOT NULL
      AND n.INSERTTIME < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
)
SELECT
    INNERCODE,
    TRADINGDAY,
    UNITNVRESTORED
FROM nav_ranked
WHERE rn = 1
ORDER BY INNERCODE, TRADINGDAY
"""
        frame = query_to_dataframe(nav_sql, max_rows=0, timeout=300)
        if frame is not None and not frame.empty:
            frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=["INNERCODE", "TRADINGDAY", "UNITNVRESTORED"])
    return pd.concat(frames, ignore_index=True)


def main():
    meta_df = query_to_dataframe(sql_query, max_rows=0, timeout=180)
    if meta_df.empty:
        raise ValueError("SQL returned empty result")

    innercodes = [int(code) for code in meta_df["INNERCODE"].dropna().unique()]
    nav_df = fetch_nav_panel(innercodes)
    if nav_df.empty:
        raise ValueError("候选基金没有可用复权净值。")

    panel_df = meta_df.merge(nav_df, on="INNERCODE", how="inner")
    if panel_df.empty:
        raise ValueError("候选基金和复权净值无法匹配。")

    date_cols = ["TRADINGDAY", "establishment_date", "scale_date"]
    for col in date_cols:
        panel_df[col] = pd.to_datetime(panel_df[col], errors="coerce")

    panel_df["share_scale_yi"] = pd.to_numeric(panel_df["share_scale_yi"], errors="coerce")
    panel_df["UNITNVRESTORED"] = pd.to_numeric(panel_df["UNITNVRESTORED"], errors="coerce")

    candidate_rows = []

    for innercode, fund_nav_df in panel_df.groupby("INNERCODE"):
        fund_nav_df = (
            fund_nav_df.sort_values("TRADINGDAY")
            .dropna(subset=["UNITNVRESTORED"])
            .drop_duplicates(subset=["TRADINGDAY"], keep="last")
        )
        if len(fund_nav_df) < 2:
            continue

        fund_meta = fund_nav_df.iloc[0]
        nav_series = fund_nav_df.set_index("TRADINGDAY")["UNITNVRESTORED"]

        nav_1y = slice_window(nav_series, ANCHOR_DATE, 1)
        nav_3y = slice_window(nav_series, ANCHOR_DATE, 3)
        nav_5y = slice_window(nav_series, ANCHOR_DATE, 5)

        if min(len(nav_1y), len(nav_3y), len(nav_5y)) < 2:
            continue

        ann_ret_1y = annualized_return(nav_1y)
        ann_ret_3y = annualized_return(nav_3y)
        max_dd_3y = max_drawdown(nav_3y)
        max_dd_5y = max_drawdown(nav_5y)

        if not np.isfinite(ann_ret_1y) or not np.isfinite(ann_ret_3y):
            continue
        if not np.isfinite(max_dd_3y) or not np.isfinite(max_dd_5y):
            continue

        if ann_ret_1y <= 0.06 or ann_ret_3y <= 0.06:
            continue
        if max_dd_3y < -0.05 or max_dd_5y < -0.05:
            continue

        candidate_rows.append(
            {
                "主基金代码": fund_meta["fund_code"],
                "基金简称": fund_meta["fund_name"],
                "基金类型": fund_meta["effective_category_name"],
                "最新规模日期": fund_meta["scale_date"].strftime("%Y-%m-%d"),
                "最新规模（亿元）": round(float(fund_meta["share_scale_yi"]), 2),
                "符合条件的现任基金经理": fund_meta["qualified_manager_names"],
                "近1年年化收益率(%)": round(float(ann_ret_1y * 100), 2),
                "近3年年化收益率(%)": round(float(ann_ret_3y * 100), 2),
                "近3年最大回撤(%)": round(float(max_dd_3y * 100), 2),
                "近5年最大回撤(%)": round(float(max_dd_5y * 100), 2),
            }
        )

    candidate_df = pd.DataFrame(candidate_rows)

    if candidate_df.empty:
        print("无符合条件的固收+备选基金")
        return candidate_df

    candidate_df = candidate_df.sort_values(
        ["近3年年化收益率(%)", "主基金代码"],
        ascending=[False, True],
    ).reset_index(drop=True)

    print(f"符合条件基金数量: {len(candidate_df)}")
    print(candidate_df.to_string(index=False))
    return candidate_df


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


__write_result_csv()
