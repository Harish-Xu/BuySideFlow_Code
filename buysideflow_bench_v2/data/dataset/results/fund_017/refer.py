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

import math
import pandas as pd

try:
    from sweagent.text2sql.db_connector import query_to_dataframe
except ImportError:
    from sweagent.text2sql.db_connector import query_to_dataframe


ANCHOR_DAY = "2026-02-12"
AS_OF_NEXT_DAY = "2026-02-13"
TARGET_FUND_CODE = "022756"
A500_INDEX_CODE = "000510"
OPEN_FUND_TYPES_SQL = "2, 3, 7"
LOOKBACK_BUFFER_DAYS = 40


TARGET_FUND_SQL = f"""
WITH params AS (
    SELECT DATE('{ANCHOR_DAY}') AS anchor_day,
           DATE('{AS_OF_NEXT_DAY}') AS as_of_next_day
)
SELECT
    fa.INNERCODE,
    fa.SECURITYCODE AS fund_code,
    COALESCE(sm.CHINAMEABBR, sm.SECUABBR) AS fund_name,
    fa.ESTABLISHMENTDATE
FROM mf_fundarchives fa
JOIN secumain sm
  ON sm.INNERCODE = fa.INNERCODE
 AND sm.SECUCATEGORY = 8
JOIN params p
  ON 1 = 1
WHERE fa.SECURITYCODE = '{TARGET_FUND_CODE}'
  AND COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
  AND fa.TYPE IN ({OPEN_FUND_TYPES_SQL})
  AND fa.INVESTMENTTYPE = 8
  AND fa.ESTABLISHMENTDATE <= p.anchor_day
  AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.anchor_day)
  AND (fa.INSERTTIME IS NULL OR fa.INSERTTIME < p.as_of_next_day)
  AND fa.XGRQ < p.as_of_next_day
  AND sm.XGRQ < p.as_of_next_day
LIMIT 1
"""


FUND_POOL_SQL_TEMPLATE = """
WITH params AS (
    SELECT
        DATE('{anchor_day}') AS anchor_day,
        DATE('{as_of_next_day}') AS as_of_next_day,
        DATE('{target_start_day}') AS target_start_day
)
SELECT
    fa.INNERCODE,
    fa.SECURITYCODE AS fund_code,
    COALESCE(sm.CHINAMEABBR, sm.SECUABBR) AS fund_name,
    fa.ESTABLISHMENTDATE
FROM mf_fundarchives fa
JOIN secumain sm
  ON sm.INNERCODE = fa.INNERCODE
 AND sm.SECUCATEGORY = 8
JOIN params p
  ON 1 = 1
WHERE fa.INVESTMENTTYPE = 8
  AND fa.TYPE IN ({open_fund_types_sql})
  AND COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
  AND fa.ESTABLISHMENTDATE IS NOT NULL
  AND fa.ESTABLISHMENTDATE <= p.target_start_day
  AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.anchor_day)
  AND (fa.INSERTTIME IS NULL OR fa.INSERTTIME < p.as_of_next_day)
  AND fa.XGRQ < p.as_of_next_day
  AND sm.XGRQ < p.as_of_next_day
  AND (
      COALESCE(sm.CHINAMEABBR, sm.SECUABBR) LIKE '%A500%增强%'
      OR fa.PERFORMANCEBENCHMARK LIKE '%中证A500%'
  )
ORDER BY fa.ESTABLISHMENTDATE, fa.SECURITYCODE
"""


INDEX_SQL = f"""
SELECT INNERCODE
FROM secumain
WHERE SECUCATEGORY = 4
  AND SECUCODE = '{A500_INDEX_CODE}'
  AND XGRQ < DATE('{AS_OF_NEXT_DAY}')
LIMIT 1
"""


NAV_SQL_TEMPLATE = """
SELECT
    INNERCODE,
    DATE(TRADINGDAY) AS trade_date,
    UNITNVRESTORED AS adj_nav,
    INSERTTIME,
    UPDATETIME,
    ID
FROM mf_fundnetvalueretrans
WHERE INNERCODE IN ({innercode_list})
  AND TRADINGDAY >= DATE('{history_start_day}')
  AND TRADINGDAY <= DATE('{anchor_day}')
  AND UNITNVRESTORED IS NOT NULL
  AND UNITNVRESTORED > 0
  AND INSERTTIME < DATE('{as_of_next_day}')
  AND UPDATETIME < DATE('{as_of_next_day}')
ORDER BY INNERCODE, TRADINGDAY, INSERTTIME, UPDATETIME, ID
"""


INDEX_QUOTE_SQL_TEMPLATE = """
SELECT
    ID,
    DATE(TRADINGDAY) AS trade_date,
    CLOSEPRICE,
    XGRQ
FROM qt_indexquote
WHERE INNERCODE = {index_innercode}
  AND TRADINGDAY >= DATE('{history_start_day}')
  AND TRADINGDAY <= DATE('{anchor_day}')
  AND CLOSEPRICE IS NOT NULL
  AND CLOSEPRICE > 0
  AND XGRQ < DATE('{as_of_next_day}')
ORDER BY TRADINGDAY, XGRQ, ID
"""


def fetch_target_fund() -> pd.Series:
    target_df = query_to_dataframe(TARGET_FUND_SQL, max_rows=10, timeout=120)
    if target_df.empty:
        raise ValueError(f"未找到目标基金 {TARGET_FUND_CODE}。")

    target_df["ESTABLISHMENTDATE"] = pd.to_datetime(target_df["ESTABLISHMENTDATE"], errors="coerce")
    target_df = target_df.dropna(subset=["INNERCODE", "fund_code", "fund_name", "ESTABLISHMENTDATE"]).copy()
    if target_df.empty:
        raise ValueError(f"目标基金 {TARGET_FUND_CODE} 缺少成立日期。")

    target_row = target_df.iloc[0].copy()
    target_row["INNERCODE"] = int(target_row["INNERCODE"])
    return target_row


def fetch_fund_pool(target_start_day: str) -> pd.DataFrame:
    sql = FUND_POOL_SQL_TEMPLATE.format(
        anchor_day=ANCHOR_DAY,
        as_of_next_day=AS_OF_NEXT_DAY,
        target_start_day=target_start_day,
        open_fund_types_sql=OPEN_FUND_TYPES_SQL,
    )
    fund_pool_df = query_to_dataframe(sql, max_rows=0, timeout=180)
    if fund_pool_df.empty:
        raise ValueError("未取到可比的开放式A500指数增强主基金样本。")

    fund_pool_df["INNERCODE"] = pd.to_numeric(fund_pool_df["INNERCODE"], errors="coerce")
    fund_pool_df["ESTABLISHMENTDATE"] = pd.to_datetime(fund_pool_df["ESTABLISHMENTDATE"], errors="coerce")
    fund_pool_df = fund_pool_df.dropna(
        subset=["INNERCODE", "fund_code", "fund_name", "ESTABLISHMENTDATE"]
    ).copy()
    fund_pool_df["INNERCODE"] = fund_pool_df["INNERCODE"].astype(int)
    return fund_pool_df.drop_duplicates(subset=["INNERCODE"]).reset_index(drop=True)


def fetch_index_innercode() -> int:
    index_df = query_to_dataframe(INDEX_SQL, max_rows=5, timeout=60)
    if index_df.empty:
        raise ValueError(f"未找到中证A500指数 {A500_INDEX_CODE}。")
    return int(index_df["INNERCODE"].iloc[0])


def fetch_nav_data(innercodes: list[int], history_start_day: str) -> pd.DataFrame:
    innercode_list = ",".join(str(code) for code in sorted(innercodes))
    sql = NAV_SQL_TEMPLATE.format(
        innercode_list=innercode_list,
        history_start_day=history_start_day,
        anchor_day=ANCHOR_DAY,
        as_of_next_day=AS_OF_NEXT_DAY,
    )
    nav_df = query_to_dataframe(sql, max_rows=0, timeout=300)
    if nav_df.empty:
        raise ValueError("未取到A500指数增强基金净值序列。")

    nav_df["INNERCODE"] = pd.to_numeric(nav_df["INNERCODE"], errors="coerce")
    nav_df["trade_date"] = pd.to_datetime(nav_df["trade_date"], errors="coerce")
    nav_df["adj_nav"] = pd.to_numeric(nav_df["adj_nav"], errors="coerce")
    nav_df["INSERTTIME"] = pd.to_datetime(nav_df["INSERTTIME"], errors="coerce")
    nav_df["UPDATETIME"] = pd.to_datetime(nav_df["UPDATETIME"], errors="coerce")
    nav_df["ID"] = pd.to_numeric(nav_df["ID"], errors="coerce")
    nav_df = nav_df.dropna(subset=["INNERCODE", "trade_date", "adj_nav"]).copy()
    nav_df = nav_df.sort_values(["INNERCODE", "trade_date", "INSERTTIME", "UPDATETIME", "ID"], kind="stable")
    nav_df = nav_df.drop_duplicates(subset=["INNERCODE", "trade_date"], keep="last")
    nav_df["INNERCODE"] = nav_df["INNERCODE"].astype(int)
    return nav_df[["INNERCODE", "trade_date", "adj_nav"]].copy()


def fetch_index_data(index_innercode: int, history_start_day: str) -> pd.DataFrame:
    sql = INDEX_QUOTE_SQL_TEMPLATE.format(
        index_innercode=index_innercode,
        history_start_day=history_start_day,
        anchor_day=ANCHOR_DAY,
        as_of_next_day=AS_OF_NEXT_DAY,
    )
    index_df = query_to_dataframe(sql, max_rows=0, timeout=180)
    if index_df.empty:
        raise ValueError("未取到中证A500指数行情。")

    index_df["trade_date"] = pd.to_datetime(index_df["trade_date"], errors="coerce")
    index_df["CLOSEPRICE"] = pd.to_numeric(index_df["CLOSEPRICE"], errors="coerce")
    index_df["XGRQ"] = pd.to_datetime(index_df["XGRQ"], errors="coerce")
    index_df["ID"] = pd.to_numeric(index_df["ID"], errors="coerce")
    index_df = index_df.dropna(subset=["trade_date", "CLOSEPRICE"]).copy()
    index_df = index_df.sort_values(["trade_date", "XGRQ", "ID"], kind="stable")
    index_df = index_df.drop_duplicates(subset=["trade_date"], keep="last")
    return index_df.rename(columns={"CLOSEPRICE": "index_close"})[["trade_date", "index_close"]].copy()


def build_ir_table(
    fund_pool_df: pd.DataFrame,
    nav_df: pd.DataFrame,
    index_df: pd.DataFrame,
    target_start_day: str,
    target_innercode: int,
) -> pd.DataFrame:
    nav_df = nav_df.sort_values(["INNERCODE", "trade_date"], kind="stable").copy()
    nav_df["fund_return"] = nav_df.groupby("INNERCODE")["adj_nav"].pct_change()

    index_df = index_df.sort_values("trade_date", kind="stable").copy()
    index_df["index_return"] = index_df["index_close"].pct_change()

    excess_df = nav_df.merge(
        index_df[["trade_date", "index_return"]],
        on="trade_date",
        how="inner",
    )
    excess_df = excess_df.dropna(subset=["fund_return", "index_return"]).copy()
    if excess_df.empty:
        raise ValueError("基金收益与指数收益没有可比的重叠交易日。")

    excess_df["excess_return"] = excess_df["fund_return"] - excess_df["index_return"]
    target_excess_dates = excess_df.loc[
        (excess_df["INNERCODE"] == target_innercode)
        & (excess_df["trade_date"] > pd.Timestamp(target_start_day)),
        "trade_date",
    ]
    if target_excess_dates.empty:
        raise ValueError("参照基金成立后没有可比的基金与指数日收益。")
    common_start_day = target_excess_dates.min()
    excess_df = excess_df[excess_df["trade_date"] >= common_start_day].copy()

    excess_df = excess_df.merge(
        fund_pool_df[["INNERCODE", "fund_code", "fund_name", "ESTABLISHMENTDATE"]],
        on="INNERCODE",
        how="inner",
    )

    ir_df = (
        excess_df.groupby(["INNERCODE", "fund_code", "fund_name", "ESTABLISHMENTDATE"], as_index=False)
        .agg(
            obs_count=("excess_return", "count"),
            excess_mean=("excess_return", "mean"),
            excess_std=("excess_return", lambda values: values.std(ddof=0)),
        )
        .copy()
    )
    ir_df = ir_df[ir_df["obs_count"] >= 60].copy()
    ir_df = ir_df[ir_df["excess_std"].notna() & (ir_df["excess_std"] > 0)].copy()
    if ir_df.empty:
        raise ValueError("没有可计算IR的A500指数增强基金。")

    ir_df["annualized_excess_return"] = ir_df["excess_mean"] * 252
    ir_df["tracking_error"] = ir_df["excess_std"] * math.sqrt(252)
    ir_df["IR"] = ir_df["annualized_excess_return"] / ir_df["tracking_error"]
    ir_df = ir_df.sort_values(["IR", "fund_code"], ascending=[False, True]).reset_index(drop=True)
    return ir_df


def main() -> pd.DataFrame:
    target_fund = fetch_target_fund()
    target_start_day = target_fund["ESTABLISHMENTDATE"].strftime("%Y-%m-%d")
    history_start_day = (
        target_fund["ESTABLISHMENTDATE"] - pd.Timedelta(days=LOOKBACK_BUFFER_DAYS)
    ).strftime("%Y-%m-%d")

    fund_pool_df = fetch_fund_pool(target_start_day)
    index_innercode = fetch_index_innercode()
    nav_df = fetch_nav_data(fund_pool_df["INNERCODE"].tolist(), history_start_day)
    index_df = fetch_index_data(index_innercode, history_start_day)

    ir_df = build_ir_table(
        fund_pool_df=fund_pool_df,
        nav_df=nav_df,
        index_df=index_df,
        target_start_day=target_start_day,
        target_innercode=int(target_fund["INNERCODE"]),
    )

    target_ir_series = ir_df.loc[ir_df["fund_code"] == TARGET_FUND_CODE, "IR"]
    if target_ir_series.empty:
        raise ValueError("目标基金没有足够数据计算IR。")
    target_ir = float(target_ir_series.iloc[0])

    output_df = ir_df[
        (ir_df["fund_code"] == TARGET_FUND_CODE) | (ir_df["IR"] > target_ir)
    ][["fund_code", "fund_name", "obs_count", "annualized_excess_return", "tracking_error", "IR"]].copy()
    output_df.columns = ["基金代码", "基金简称", "有效样本天数", "年化超额收益", "跟踪误差", "IR"]
    output_df["有效样本天数"] = output_df["有效样本天数"].astype(int)
    output_df["年化超额收益"] = (output_df["年化超额收益"] * 100).round(2)
    output_df["跟踪误差"] = (output_df["跟踪误差"] * 100).round(2)
    output_df["IR"] = output_df["IR"].round(4)
    output_df = output_df.sort_values(["IR", "基金代码"], ascending=[False, True]).reset_index(drop=True)

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
