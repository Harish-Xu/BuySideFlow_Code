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
from sweagent.text2sql.db_connector import query_to_dataframe

CHECK_DATE = "2025-12-19"
WINDOW_DAYS = 20
ALERT_RATIO = 0.5
QUERY_TIMEOUT_SECONDS = 120
TARGET_FUNDS = [
    "000107.OF",
    "000385.OF",
    "000171.OF",
    "110008.OF",
    "420102.OF",
    "001570.OF",
    "002522.OF",
]


def build_sql(check_date: str, window_days: int, fund_codes: list[str]) -> str:
    normalized_codes = [code.replace(".OF", "") for code in fund_codes]
    code_list_sql = ", ".join(f"'{code}'" for code in normalized_codes)

    return f"""
WITH trade_days AS (
    SELECT t.trading_day
    FROM (
        SELECT DISTINCT td.TRADINGDATE AS trading_day
        FROM qt_tradingdaynew td
        WHERE td.IFTRADINGDAY = 1
          AND td.SECUMARKET = 83
          AND td.TRADINGDATE <= DATE('{check_date}')
        ORDER BY td.TRADINGDATE DESC
        LIMIT {int(window_days) + 1}
    ) t
),
fund_list AS (
    SELECT
        fa.INNERCODE,
        fa.SECUCODE AS fund_code,
        COALESCE(sm.CHINAMEABBR, sm.SECUABBR, fa.SECUCODE) AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
    WHERE fa.SECUCODE IN ({code_list_sql})
      AND sm.SECUCATEGORY = 8
),
hs300_inner AS (
    SELECT s.INNERCODE
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE = '000300'
    ORDER BY s.INNERCODE
    LIMIT 1
),
fund_price_ranked AS (
    SELECT
        fl.fund_code,
        fl.fund_name,
        f.TRADINGDAY AS trading_day,
        f.UNITNVRESTORED AS fund_price,
        ROW_NUMBER() OVER (
            PARTITION BY f.INNERCODE, f.TRADINGDAY
            ORDER BY f.INSERTTIME DESC, f.ID DESC
        ) AS rn
    FROM fund_list fl
    JOIN mf_fundnetvalueretrans f
      ON f.INNERCODE = fl.INNERCODE
    JOIN trade_days td
      ON td.trading_day = f.TRADINGDAY
    WHERE f.UNITNVRESTORED IS NOT NULL
      AND f.UNITNVRESTORED > 0
      AND f.INSERTTIME < DATE_ADD(DATE('{check_date}'), INTERVAL 1 DAY)
),
fund_price AS (
    SELECT fund_code, fund_name, trading_day, fund_price
    FROM fund_price_ranked
    WHERE rn = 1
),
index_price AS (
    SELECT
        q.TRADINGDAY AS trading_day,
        q.CLOSEPRICE AS hs300_price
    FROM qt_indexquote q
    JOIN hs300_inner hi
      ON hi.INNERCODE = q.INNERCODE
    JOIN trade_days td
      ON td.trading_day = q.TRADINGDAY
    WHERE q.CLOSEPRICE IS NOT NULL
      AND q.CLOSEPRICE > 0
)
SELECT
    fp.fund_code,
    fp.fund_name,
    fp.trading_day,
    fp.fund_price,
    ip.hs300_price
FROM fund_price fp
JOIN index_price ip
  ON ip.trading_day = fp.trading_day
ORDER BY fp.fund_code, fp.trading_day
"""


def calc_annualized_vol(price_series: pd.Series, min_ret_count: int) -> tuple[float, int]:
    returns = price_series.pct_change().dropna()
    ret_cnt = int(len(returns))
    if ret_cnt < min_ret_count:
        return np.nan, ret_cnt
    return float(returns.std(ddof=0) * np.sqrt(252)), ret_cnt


def run_vol_alert(
    check_date: str = CHECK_DATE,
    window_days: int = WINDOW_DAYS,
    alert_ratio: float = ALERT_RATIO,
) -> pd.DataFrame:
    sql = build_sql(check_date=check_date, window_days=window_days, fund_codes=TARGET_FUNDS)
    raw_df = query_to_dataframe(sql, timeout=QUERY_TIMEOUT_SECONDS)
    if raw_df.empty:
        raise ValueError("未获取到基金与沪深300的有效价格序列")
    required_ret_count = max(int(window_days), 1)

    raw_df["trading_day"] = pd.to_datetime(raw_df["trading_day"])
    raw_df["fund_price"] = pd.to_numeric(raw_df["fund_price"], errors="coerce")
    raw_df["hs300_price"] = pd.to_numeric(raw_df["hs300_price"], errors="coerce")
    raw_df = raw_df.dropna(subset=["fund_price", "hs300_price"])
    if raw_df.empty:
        raise ValueError("价格序列清洗后为空")

    hs300_series = (
        raw_df[["trading_day", "hs300_price"]]
        .drop_duplicates(subset=["trading_day"])
        .sort_values("trading_day")
    )
    hs300_vol, hs300_ret_cnt = calc_annualized_vol(
        hs300_series["hs300_price"], min_ret_count=required_ret_count
    )
    if pd.isna(hs300_vol):
        raise ValueError(f"沪深300有效收益样本不足，当前仅{hs300_ret_cnt}天")

    rows = []
    for (fund_code, fund_name), grp in raw_df.groupby(["fund_code", "fund_name"], sort=False):
        g = grp.sort_values("trading_day").drop_duplicates(subset=["trading_day"])
        fund_vol, fund_ret_cnt = calc_annualized_vol(
            g["fund_price"], min_ret_count=required_ret_count
        )
        if pd.isna(fund_vol):
            continue

        ratio = fund_vol / hs300_vol if hs300_vol > 0 else np.nan
        if pd.notna(ratio) and ratio >= alert_ratio:
            rows.append(
                {
                    "观察日": check_date,
                    "基金代码": fund_code,
                    "基金简称": fund_name,
                    "基金年化波动率": fund_vol,
                    "沪深300年化波动率": hs300_vol,
                    "相对比例": ratio,
                }
            )

    out = pd.DataFrame(
        rows,
        columns=["观察日", "基金代码", "基金简称", "基金年化波动率", "沪深300年化波动率", "相对比例"],
    )
    if out.empty:
        return out

    for col in ["基金年化波动率", "沪深300年化波动率", "相对比例"]:
        out[col] = out[col].astype(float).round(6)
    return out.sort_values(["相对比例", "基金代码"], ascending=[False, True]).reset_index(drop=True)


if __name__ == "__main__":
    result = run_vol_alert()
    if result.empty:
        print("未触发预警")
    else:
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
