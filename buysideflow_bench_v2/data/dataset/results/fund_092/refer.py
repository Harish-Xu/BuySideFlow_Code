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
from collections import OrderedDict

try:
    from sweagent.text2sql.db_connector import query_to_dataframe
except ImportError:
    from sweagent.text2sql.db_connector import query_to_dataframe

SQL_QUERY = """
WITH params AS (
    SELECT
        DATE('2025-09-30') AS report_date,
        DATE('2026-02-24') AS asof_date
),
a_share_securities AS (
    SELECT DISTINCT
        sm.INNERCODE,
        sm.COMPANYCODE,
        sm.LISTEDSECTOR
    FROM secumain sm
    JOIN params p
      ON 1 = 1
    WHERE sm.SECUCATEGORY = 1
      AND sm.SECUMARKET IN (83, 90)
      AND sm.COMPANYCODE IS NOT NULL
      AND sm.LISTEDDATE <= p.report_date
),
list_status_ranked AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        s.LISTEDSECTOR,
        ls.CHANGETYPE,
        ROW_NUMBER() OVER (
            PARTITION BY s.INNERCODE
            ORDER BY ls.CHANGEDATE DESC,
                     COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
                     ls.JSID DESC,
                     ls.ID DESC
        ) AS rn
    FROM a_share_securities s
    JOIN (
        SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID
        FROM lc_liststatus
        WHERE CHANGETYPE BETWEEN 1 AND 9
        UNION ALL
        SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID
        FROM lc_stibliststatus
        WHERE CHANGETYPE BETWEEN 1 AND 9
    ) ls
      ON ls.INNERCODE = s.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE ls.CHANGETYPE BETWEEN 1 AND 9
      AND ls.CHANGEDATE < DATE_ADD(p.report_date, INTERVAL 1 DAY)
      AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME < DATE_ADD(p.asof_date, INTERVAL 1 DAY))
),
listed_securities AS (
    SELECT INNERCODE, COMPANYCODE, LISTEDSECTOR
    FROM list_status_ranked
    WHERE rn = 1
      AND CHANGETYPE IN (1, 3)
),
industry_raw AS (
    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYCODE,
        e.FIRSTINDUSTRYNAME,
        e.INFOPUBLDATE,
        e.INSERTTIME,
        1 AS source_priority,
        e.ID
    FROM lc_exgindustry e
    JOIN (SELECT DISTINCT COMPANYCODE FROM listed_securities) s
      ON s.COMPANYCODE = e.COMPANYCODE
    JOIN params p
      ON 1 = 1
    WHERE e.STANDARD = 38
      AND e.INFOPUBLDATE < DATE_ADD(p.report_date, INTERVAL 1 DAY)
      AND e.INSERTTIME < DATE_ADD(p.report_date, INTERVAL 1 DAY)
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.report_date)

    UNION ALL

    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYCODE,
        e.FIRSTINDUSTRYNAME,
        e.INFOPUBLDATE,
        e.INSERTTIME,
        2 AS source_priority,
        e.ID
    FROM lc_stibexgindustry e
    JOIN (SELECT DISTINCT COMPANYCODE FROM listed_securities) s
      ON s.COMPANYCODE = e.COMPANYCODE
    JOIN params p
      ON 1 = 1
    WHERE e.STANDARD = 38
      AND e.EFFECTIVEDATE <= p.report_date
      AND e.INFOPUBLDATE < DATE_ADD(p.report_date, INTERVAL 1 DAY)
      AND e.INSERTTIME < DATE_ADD(p.report_date, INTERVAL 1 DAY)
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.report_date)
),
industry_company_asof AS (
    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYCODE,
        ROW_NUMBER() OVER (
            PARTITION BY e.COMPANYCODE
            ORDER BY e.INFOPUBLDATE DESC,
                     e.INSERTTIME DESC,
                     e.source_priority ASC,
                     e.ID DESC
        ) AS rn
    FROM industry_raw e
),
industry_code_name_asof AS (
    SELECT
        e.FIRSTINDUSTRYCODE,
        e.FIRSTINDUSTRYNAME,
        ROW_NUMBER() OVER (
            PARTITION BY e.FIRSTINDUSTRYCODE
            ORDER BY e.INFOPUBLDATE DESC,
                     e.INSERTTIME DESC,
                     e.source_priority ASC,
                     e.ID DESC
        ) AS rn
    FROM industry_raw e
),
profit_company_raw AS (
    SELECT
        m.COMPANYCODE,
        m.NPFROMPARENTCOMPANYOWNERS,
        CASE m.MARK
            WHEN 1 THEN 1
            WHEN 8 THEN 2
            WHEN 7 THEN 3
            WHEN 6 THEN 4
            WHEN 2 THEN 5
            ELSE 99
        END AS report_priority,
        m.INFOPUBLDATE,
        m.INSERTTIME,
        1 AS source_priority,
        m.ID
    FROM lc_maindatanew m
    JOIN (SELECT DISTINCT COMPANYCODE FROM listed_securities WHERE LISTEDSECTOR <> 7) s
      ON s.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON 1 = 1
    WHERE m.ENDDATE >= p.report_date
      AND m.ENDDATE < DATE_ADD(p.report_date, INTERVAL 1 DAY)
      AND m.BULLETINTYPE = '20'
      AND m.MARK IN (1, 8, 7, 6, 2)
      AND m.NPFROMPARENTCOMPANYOWNERS IS NOT NULL
      AND m.INFOPUBLDATE < DATE_ADD(p.asof_date, INTERVAL 1 DAY)
      AND (m.INSERTTIME IS NULL OR m.INSERTTIME < DATE_ADD(p.asof_date, INTERVAL 1 DAY))

    UNION ALL

    SELECT
        m.COMPANYCODE,
        m.NPPARENTCOMPANYOWNERS AS NPFROMPARENTCOMPANYOWNERS,
        1 AS report_priority,
        m.INFOPUBLDATE,
        m.INSERTTIME,
        2 AS source_priority,
        m.ID
    FROM lc_stibmainindex m
    JOIN (SELECT DISTINCT COMPANYCODE FROM listed_securities WHERE LISTEDSECTOR = 7) s
      ON s.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON 1 = 1
    WHERE m.ENDDATE >= p.report_date
      AND m.ENDDATE < DATE_ADD(p.report_date, INTERVAL 1 DAY)
      AND m.IFMERGED = 1
      AND m.IFADJUSTED = 2
      AND m.NPPARENTCOMPANYOWNERS IS NOT NULL
      AND m.INFOPUBLDATE < DATE_ADD(p.asof_date, INTERVAL 1 DAY)
      AND m.INSERTTIME < DATE_ADD(p.asof_date, INTERVAL 1 DAY)
),
profit_company_asof AS (
    SELECT
        m.COMPANYCODE,
        m.NPFROMPARENTCOMPANYOWNERS,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE
            ORDER BY m.report_priority,
                     COALESCE(m.INFOPUBLDATE, DATE('1900-01-01')) DESC,
                     m.INSERTTIME DESC,
                     m.source_priority DESC,
                     m.ID DESC
        ) AS rn
    FROM profit_company_raw m
),
industry_profit AS (
    SELECT
        ic.FIRSTINDUSTRYCODE,
        SUM(CASE WHEN pc.NPFROMPARENTCOMPANYOWNERS > 0 THEN 1 ELSE 0 END) AS profitable_companies,
        COUNT(*) AS total_companies,
        SUM(CASE WHEN pc.NPFROMPARENTCOMPANYOWNERS > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0) AS profit_ratio
    FROM industry_company_asof ic
    JOIN profit_company_asof pc
      ON pc.COMPANYCODE = ic.COMPANYCODE
     AND pc.rn = 1
    WHERE ic.rn = 1
    GROUP BY ic.FIRSTINDUSTRYCODE
),
top_industry AS (
    SELECT
        ip.FIRSTINDUSTRYCODE,
        cn.FIRSTINDUSTRYNAME,
        ip.profit_ratio,
        ip.total_companies
    FROM industry_profit ip
    JOIN industry_code_name_asof cn
      ON cn.FIRSTINDUSTRYCODE = ip.FIRSTINDUSTRYCODE
     AND cn.rn = 1
    WHERE cn.FIRSTINDUSTRYNAME NOT IN ('银行', '非银金融')
    ORDER BY ip.profit_ratio DESC, ip.total_companies DESC, ip.FIRSTINDUSTRYCODE ASC
    LIMIT 1
),
industry_stocks AS (
    SELECT DISTINCT
        s.INNERCODE,
        ic.FIRSTINDUSTRYCODE
    FROM industry_company_asof ic
    JOIN listed_securities s
      ON s.COMPANYCODE = ic.COMPANYCODE
    WHERE ic.rn = 1
),
fund_pool AS (
    SELECT
        fa.INNERCODE,
        fs.SECUCODE,
        fs.SECUABBR
    FROM mf_fundarchives fa
    JOIN secumain fs
      ON fs.INNERCODE = fa.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE fs.SECUCATEGORY = 8
      AND fa.TYPE = 4
      AND COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= p.report_date
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > p.report_date)
),
portfolio_stock_asof AS (
    SELECT
        p.INNERCODE,
        p.SECUINNERCODE,
        COALESCE(p.RATIOINNV, 0) AS ratio_in_nv,
        ROW_NUMBER() OVER (
            PARTITION BY p.INNERCODE, p.SECUINNERCODE
            ORDER BY COALESCE(p.INFOPUBLDATE, DATE('1900-01-01')) DESC,
                     p.INSERTTIME DESC,
                     p.ID DESC
        ) AS rn
    FROM mf_portfoliodetailsall p
    JOIN fund_pool fp
      ON fp.INNERCODE = p.INNERCODE
    JOIN params prm
      ON 1 = 1
    WHERE p.ENDDATE >= prm.report_date
      AND p.ENDDATE < DATE_ADD(prm.report_date, INTERVAL 1 DAY)
      AND p.INVESTOBJECT = 1
      AND p.INFOPUBLDATE < DATE_ADD(prm.asof_date, INTERVAL 1 DAY)
      AND p.INSERTTIME < DATE_ADD(prm.asof_date, INTERVAL 1 DAY)
)
SELECT
    ti.FIRSTINDUSTRYNAME AS top_industry_name,
    ROUND(ti.profit_ratio * 100, 2) AS profit_ratio,
    fp.SECUCODE AS fund_code,
    fp.SECUABBR AS fund_name
FROM top_industry ti
JOIN industry_stocks s
  ON s.FIRSTINDUSTRYCODE = ti.FIRSTINDUSTRYCODE
JOIN portfolio_stock_asof ps
  ON ps.SECUINNERCODE = s.INNERCODE
 AND ps.rn = 1
JOIN fund_pool fp
  ON fp.INNERCODE = ps.INNERCODE
GROUP BY ti.FIRSTINDUSTRYCODE, ti.FIRSTINDUSTRYNAME, ti.profit_ratio, fp.SECUCODE, fp.SECUABBR
ORDER BY SUM(ps.ratio_in_nv) DESC, fp.SECUCODE ASC
LIMIT 1
"""

df = query_to_dataframe(SQL_QUERY, timeout=120)
required_columns = {"top_industry_name", "profit_ratio", "fund_code", "fund_name"}
if set(df.columns) != required_columns:
    raise ValueError(f"Unexpected columns: {list(df.columns)}")
if len(df) != 1:
    raise ValueError(f"Expected exactly one row, got {len(df)}")

row = df.iloc[0]
result = OrderedDict(
    [
        ("top_industry_name", str(row["top_industry_name"])),
        ("profit_ratio", round(float(row["profit_ratio"]), 2)),
        ("fund_code", str(row["fund_code"])),
        ("fund_name", str(row["fund_name"])),
    ]
)
print(json.dumps(result, ensure_ascii=False))
__result_obj = pd.DataFrame(
    [result],
    columns=["top_industry_name", "profit_ratio", "fund_code", "fund_name"],
).rename(
    columns={
        "top_industry_name": "行业名称",
        "profit_ratio": "盈利占比",
        "fund_code": "基金代码",
        "fund_name": "基金名称",
    }
)


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
