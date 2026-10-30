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

FUND_CODE = "004881"
INDEX_CODE = "000300"
REPORT_DATES = ["2024-06-30", "2024-12-31", "2025-06-30"]
REPORT_IN_SQL = ", ".join(f"'{d}'" for d in REPORT_DATES)
DATA_CUTOFF = "2025-08-31"


def query_fund_industry_weights(fund_code: str, report_dates: list[str]) -> pd.DataFrame:
    sql = f"""
    WITH fund_holdings AS (
        SELECT
            sp.REPORTDATE,
            sp.STOCKINNERCODE,
            sp.RATIOINNV,
            stk.COMPANYCODE
        FROM mf_stockportfoliodetail sp
        JOIN secumain fund_sm
          ON fund_sm.INNERCODE = sp.INNERCODE
         AND fund_sm.SECUCODE = '{fund_code}'
         AND fund_sm.SECUCATEGORY = 8
        JOIN secumain stk
          ON stk.INNERCODE = sp.STOCKINNERCODE
        WHERE sp.REPORTDATE IN ({REPORT_IN_SQL})
          AND sp.RATIOINNV IS NOT NULL
          AND sp.INFOPUBLDATE < DATE_ADD(DATE('{DATA_CUTOFF}'), INTERVAL 1 DAY)
          AND sp.INSERTTIME < DATE_ADD(DATE('{DATA_CUTOFF}'), INTERVAL 1 DAY)
    ),
    industry_candidates AS (
        SELECT
            fh.REPORTDATE,
            fh.STOCKINNERCODE,
            ei.FIRSTINDUSTRYNAME,
            1 AS src_rank,
            ei.INFOPUBLDATE AS effective_date,
            ei.XGRQ AS updated_at,
            ei.JSID
        FROM fund_holdings fh
        JOIN lc_exgindustry ei
          ON ei.COMPANYCODE = fh.COMPANYCODE
         AND ei.STANDARD = 38
         AND ei.INFOPUBLDATE <= fh.REPORTDATE
         AND (ei.CANCELDATE IS NULL OR ei.CANCELDATE > fh.REPORTDATE)
         AND (ei.INSERTTIME IS NULL OR ei.INSERTTIME < DATE_ADD(DATE('{DATA_CUTOFF}'), INTERVAL 1 DAY))
        UNION ALL
        SELECT
            fh.REPORTDATE,
            fh.STOCKINNERCODE,
            stib.FIRSTINDUSTRYNAME,
            2 AS src_rank,
            stib.EFFECTIVEDATE AS effective_date,
            stib.UPDATETIME AS updated_at,
            stib.JSID
        FROM fund_holdings fh
        JOIN lc_stibexgindustry stib
          ON stib.COMPANYCODE = fh.COMPANYCODE
         AND stib.STANDARD = 38
         AND stib.EFFECTIVEDATE <= fh.REPORTDATE
         AND (stib.CANCELDATE IS NULL OR stib.CANCELDATE > fh.REPORTDATE)
         AND stib.INFOPUBLDATE < DATE_ADD(DATE('{DATA_CUTOFF}'), INTERVAL 1 DAY)
         AND stib.INSERTTIME < DATE_ADD(DATE('{DATA_CUTOFF}'), INTERVAL 1 DAY)
    ),
    industry_asof AS (
        SELECT
            REPORTDATE,
            STOCKINNERCODE,
            FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY REPORTDATE, STOCKINNERCODE
                ORDER BY src_rank ASC, effective_date DESC, updated_at DESC, JSID DESC
            ) AS rn
        FROM industry_candidates
    )
    SELECT
        fh.REPORTDATE,
        COALESCE(ia.FIRSTINDUSTRYNAME, '未披露') AS industry_name,
        ROUND(SUM(fh.RATIOINNV) * 100, 4) AS fund_weight_pct
    FROM fund_holdings fh
    LEFT JOIN industry_asof ia
      ON ia.REPORTDATE = fh.REPORTDATE
     AND ia.STOCKINNERCODE = fh.STOCKINNERCODE
     AND ia.rn = 1
    GROUP BY fh.REPORTDATE, COALESCE(ia.FIRSTINDUSTRYNAME, '未披露')
    ORDER BY fh.REPORTDATE, industry_name
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=300)
    if df.empty:
        raise ValueError("基金行业权重数据为空。")

    df["REPORTDATE"] = pd.to_datetime(df["REPORTDATE"], errors="coerce")
    df["fund_weight_pct"] = pd.to_numeric(df["fund_weight_pct"], errors="coerce")
    df = df.dropna(subset=["REPORTDATE", "industry_name", "fund_weight_pct"]).copy()

    expected_dates = set(pd.to_datetime(report_dates))
    actual_dates = set(df["REPORTDATE"].drop_duplicates())
    missing_dates = sorted(expected_dates - actual_dates)
    if missing_dates:
        raise ValueError(f"基金缺少报告期持仓数据: {missing_dates}")

    return df


def query_index_industry_weights(index_code: str, report_dates: list[str]) -> pd.DataFrame:
    sql = f"""
    WITH report_dates AS (
        SELECT DATE('2024-06-30') AS report_date
        UNION ALL SELECT DATE('2024-12-31')
        UNION ALL SELECT DATE('2025-06-30')
    ),
    index_meta AS (
        SELECT sm.INNERCODE AS index_innercode
        FROM secumain sm
        WHERE sm.SECUCODE = '{index_code}'
          AND sm.SECUCATEGORY = 4
          AND sm.LISTEDSECTOR = 1
    ),
    weight_dates AS (
        SELECT
            rd.report_date,
            MAX(w.ENDDATE) AS weight_date
        FROM report_dates rd
        JOIN index_meta im
          ON 1 = 1
        JOIN lc_indexcomponentsweight w
          ON w.INDEXCODE = im.index_innercode
         AND w.ENDDATE <= rd.report_date
        GROUP BY rd.report_date
    ),
    index_components AS (
        SELECT
            wd.report_date,
            w.INNERCODE AS stock_innercode,
            w.WEIGHT,
            stk.COMPANYCODE,
            wd.weight_date
        FROM weight_dates wd
        JOIN index_meta im
          ON 1 = 1
        JOIN lc_indexcomponentsweight w
          ON w.INDEXCODE = im.index_innercode
         AND w.ENDDATE = wd.weight_date
        JOIN secumain stk
          ON stk.INNERCODE = w.INNERCODE
    ),
    industry_candidates AS (
        SELECT
            ic.report_date,
            ic.stock_innercode,
            ei.FIRSTINDUSTRYNAME,
            1 AS src_rank,
            ei.INFOPUBLDATE AS effective_date,
            ei.XGRQ AS updated_at,
            ei.JSID
        FROM index_components ic
        JOIN lc_exgindustry ei
          ON ei.COMPANYCODE = ic.COMPANYCODE
         AND ei.STANDARD = 38
         AND ei.INFOPUBLDATE <= ic.weight_date
         AND (ei.CANCELDATE IS NULL OR ei.CANCELDATE > ic.weight_date)
         AND (ei.INSERTTIME IS NULL OR ei.INSERTTIME < DATE_ADD(DATE('{DATA_CUTOFF}'), INTERVAL 1 DAY))
        UNION ALL
        SELECT
            ic.report_date,
            ic.stock_innercode,
            stib.FIRSTINDUSTRYNAME,
            2 AS src_rank,
            stib.EFFECTIVEDATE AS effective_date,
            stib.UPDATETIME AS updated_at,
            stib.JSID
        FROM index_components ic
        JOIN lc_stibexgindustry stib
          ON stib.COMPANYCODE = ic.COMPANYCODE
         AND stib.STANDARD = 38
         AND stib.EFFECTIVEDATE <= ic.weight_date
         AND (stib.CANCELDATE IS NULL OR stib.CANCELDATE > ic.weight_date)
         AND stib.INFOPUBLDATE < DATE_ADD(DATE('{DATA_CUTOFF}'), INTERVAL 1 DAY)
         AND stib.INSERTTIME < DATE_ADD(DATE('{DATA_CUTOFF}'), INTERVAL 1 DAY)
    ),
    industry_asof AS (
        SELECT
            report_date,
            stock_innercode,
            FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY report_date, stock_innercode
                ORDER BY src_rank ASC, effective_date DESC, updated_at DESC, JSID DESC
            ) AS rn
        FROM industry_candidates
    )
    SELECT
        ic.report_date AS REPORTDATE,
        COALESCE(ia.FIRSTINDUSTRYNAME, '未披露') AS industry_name,
        ROUND(SUM(ic.WEIGHT), 4) AS index_weight_pct
    FROM index_components ic
    LEFT JOIN industry_asof ia
      ON ia.report_date = ic.report_date
     AND ia.stock_innercode = ic.stock_innercode
     AND ia.rn = 1
    GROUP BY ic.report_date, COALESCE(ia.FIRSTINDUSTRYNAME, '未披露')
    ORDER BY ic.report_date, industry_name
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=300)
    if df.empty:
        raise ValueError("指数行业权重数据为空。")

    df["REPORTDATE"] = pd.to_datetime(df["REPORTDATE"], errors="coerce")
    df["index_weight_pct"] = pd.to_numeric(df["index_weight_pct"], errors="coerce")
    df = df.dropna(subset=["REPORTDATE", "industry_name", "index_weight_pct"]).copy()

    expected_dates = set(pd.to_datetime(report_dates))
    actual_dates = set(df["REPORTDATE"].drop_duplicates())
    missing_dates = sorted(expected_dates - actual_dates)
    if missing_dates:
        raise ValueError(f"指数缺少报告期权重数据: {missing_dates}")

    return df


def build_active_weight_table(
    fund_weights: pd.DataFrame,
    index_weights: pd.DataFrame,
    report_dates: list[str],
) -> pd.DataFrame:
    merged = fund_weights.merge(
        index_weights,
        on=["REPORTDATE", "industry_name"],
        how="outer",
    ).fillna({"fund_weight_pct": 0, "index_weight_pct": 0})
    merged["active_weight_pct"] = merged["fund_weight_pct"] - merged["index_weight_pct"]

    report_dates_dt = pd.to_datetime(report_dates)
    all_industries = sorted(merged["industry_name"].dropna().unique())
    full_grid = pd.MultiIndex.from_product(
        [all_industries, report_dates_dt],
        names=["industry_name", "REPORTDATE"],
    ).to_frame(index=False)

    full = full_grid.merge(
        merged[["industry_name", "REPORTDATE", "active_weight_pct"]],
        on=["industry_name", "REPORTDATE"],
        how="left",
    ).fillna({"active_weight_pct": 0})

    pivot = full.pivot(
        index="industry_name",
        columns="REPORTDATE",
        values="active_weight_pct",
    ).reset_index()
    pivot.columns = ["industry_name"] + [
        col.strftime("%Y-%m-%d") for col in pivot.columns[1:]
    ]

    avg = (
        full.groupby("industry_name", as_index=False)["active_weight_pct"]
        .mean()
        .rename(columns={"active_weight_pct": "period_avg_pct"})
    )

    result = (
        pivot.merge(avg, on="industry_name", how="left")
        .sort_values(["period_avg_pct", "industry_name"], ascending=[True, True])
        .reset_index(drop=True)
    )

    numeric_cols = [col for col in result.columns if col != "industry_name"]
    result[numeric_cols] = result[numeric_cols].apply(pd.to_numeric, errors="coerce").round(4)
    return result


def main() -> None:
    fund_weights = query_fund_industry_weights(FUND_CODE, REPORT_DATES)
    index_weights = query_index_industry_weights(INDEX_CODE, REPORT_DATES)
    result = build_active_weight_table(fund_weights, index_weights, REPORT_DATES).rename(
        columns={"industry_name": "行业", "period_avg_pct": "区间均值"}
    )
    print(result.to_string(index=False))
    return result


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
    result_df.to_csv("result.csv", index=False, encoding="utf-8-sig")
    __cleanup_extra_csv()
    __finalize_picture()


__write_result_csv()
