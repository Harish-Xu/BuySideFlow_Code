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
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

try:
    from sweagent.text2sql.db_connector import query_to_dataframe
except ImportError:
    from sweagent.text2sql.db_connector import query_to_dataframe

BACKTEST_START = "2025-01-01"
BACKTEST_END = "2025-12-31"
SEED_BOUNDARY = "2024-12-31"
NAV_FETCH_START = "2024-01-01"
ANN_FACTOR = 252
OUTPUT_PNG = Path(__file__).with_name("picture.png")

CORE_FUNDS = ["022670", "022432", "022485"]
SAT_FUNDS = ["020670", "010612", "012420", "020273", "013171"]
ALL_FUNDS = CORE_FUNDS + SAT_FUNDS

NAV_SQL = f"""
WITH params AS (
    SELECT DATE('{NAV_FETCH_START}') AS fetch_start,
           DATE('{BACKTEST_END}') AS end_date
),
nav_dedup AS (
    SELECT
        s.SECUCODE,
        s.SECUABBR,
        DATE(f.TRADINGDAY) AS trading_day,
        f.UNITNVRESTORED,
        ROW_NUMBER() OVER (
            PARTITION BY s.SECUCODE, DATE(f.TRADINGDAY)
            ORDER BY f.TRADINGDAY DESC, f.INSERTTIME DESC, f.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans f
    JOIN secumain s
      ON s.INNERCODE = f.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE s.SECUCATEGORY = 8
      AND s.SECUCODE IN ({', '.join(repr(code) for code in ALL_FUNDS)})
      AND f.TRADINGDAY >= p.fetch_start
      AND f.TRADINGDAY < DATE_ADD(p.end_date, INTERVAL 1 DAY)
      AND f.UNITNVRESTORED IS NOT NULL
)
SELECT
    SECUCODE,
    SECUABBR,
    trading_day,
    UNITNVRESTORED
FROM nav_dedup
WHERE rn = 1
ORDER BY trading_day, SECUCODE
"""

CALENDAR_SQL = f"""
SELECT MAX(TRADINGDATE) AS rebalance_date
FROM qt_tradingdaynew
WHERE IFTRADINGDAY = 1
  AND SECUMARKET IN (83, 90)
  AND TRADINGDATE >= '{BACKTEST_START}'
  AND TRADINGDATE <= '{BACKTEST_END}'
GROUP BY YEAR(TRADINGDATE), QUARTER(TRADINGDATE)
ORDER BY rebalance_date
"""

nav = query_to_dataframe(NAV_SQL, max_rows=0)
if nav.empty:
    raise ValueError("未取到基金净值数据，请核查基金代码和日期区间。")

nav["trading_day"] = pd.to_datetime(nav["trading_day"])
nav["UNITNVRESTORED"] = pd.to_numeric(nav["UNITNVRESTORED"], errors="coerce")
nav.loc[nav['UNITNVRESTORED'] <= 0, 'UNITNVRESTORED'] = np.nan
nav = nav.dropna(subset=["UNITNVRESTORED"]).copy()
nav = nav.sort_values(["SECUCODE", "trading_day"]).reset_index(drop=True)
nav_panel = nav.pivot(index="trading_day", columns="SECUCODE", values="UNITNVRESTORED").sort_index()
missing_codes = [code for code in ALL_FUNDS if code not in nav_panel.columns]
if missing_codes:
    raise ValueError(f"以下基金缺少净值序列: {missing_codes}")

nav_panel = nav_panel[ALL_FUNDS]
nav_panel = nav_panel[nav_panel.index <= BACKTEST_END].dropna(how="any")
seed_candidates = nav_panel[nav_panel.index <= SEED_BOUNDARY]
if seed_candidates.empty:
    raise ValueError("8只基金在2024-12-31或之前没有共同可用复权单位净值日。")
seed_date = seed_candidates.index[-1]
nav_panel = nav_panel[nav_panel.index >= seed_date]

return_panel = nav_panel.pct_change().dropna(how="any")
return_panel = return_panel[
    (return_panel.index >= BACKTEST_START) & (return_panel.index <= BACKTEST_END)
].copy()
if return_panel.empty:
    raise ValueError("8 只基金在回测区间内没有共同可比收益率。")

calendar = query_to_dataframe(CALENDAR_SQL)
if calendar.empty:
    raise ValueError("未取到季度末交易日。")
calendar["rebalance_date"] = pd.to_datetime(calendar["rebalance_date"])
rebalance_dates = calendar['rebalance_date'].tolist()
missing_rebalance = [dt for dt in rebalance_dates if dt not in return_panel.index]
if missing_rebalance:
    raise ValueError(f'Quarter-end rebalancing requires common valid NAV: {missing_rebalance}')
rebalance_set = set(rebalance_dates)

weights = pd.Series(
    {**{code: 0.70 / len(CORE_FUNDS) for code in CORE_FUNDS},
     **{code: 0.30 / len(SAT_FUNDS) for code in SAT_FUNDS}},
    dtype=float,
)
positions = weights.copy()
portfolio_points = [(seed_date, 1.0)]

for trade_date, row in return_panel.iterrows():
    positions = positions * (1.0 + row[weights.index])
    total_nav = float(positions.sum())
    portfolio_points.append((trade_date, total_nav))
    if trade_date in rebalance_set:
        positions = weights * total_nav

nav_series = pd.Series(dict(portfolio_points)).sort_index()
daily_returns = nav_series.pct_change().dropna()
if daily_returns.empty:
    raise ValueError("组合收益序列为空，无法计算绩效指标。")

annualized_return = float(nav_series.iloc[-1] ** (ANN_FACTOR / len(daily_returns)) - 1)
annualized_volatility = float(daily_returns.std(ddof=0) * math.sqrt(ANN_FACTOR))
daily_volatility = float(daily_returns.std(ddof=0))
sharpe_ratio = (
    float(daily_returns.mean() / daily_volatility * math.sqrt(ANN_FACTOR))
    if daily_volatility > 0
    else 0.0
)
cummax = nav_series.cummax()
drawdown = nav_series / cummax - 1.0
max_drawdown = float(drawdown.min())

OUTPUT_PNG.parent.mkdir(parents=True, exist_ok=True)
fig, axes = plt.subplots(2, 1, figsize=(14, 10), gridspec_kw={"height_ratios": [3, 1]})

ax_nav = axes[0]
ax_nav.plot(nav_series.index, nav_series.values, color="#1f77b4", linewidth=2)
ax_nav.axhline(y=1.0, color="gray", linestyle="--", alpha=0.5)
for rebalance_date in rebalance_dates:
    ax_nav.axvline(x=rebalance_date, color="#d62728", linestyle=":", alpha=0.45)
ax_nav.set_title("70% Core + 30% Satellite NAV (2025)")
ax_nav.set_ylabel("NAV")
ax_nav.grid(True, alpha=0.3)

ax_dd = axes[1]
ax_dd.fill_between(drawdown.index, drawdown.values * 100, 0, color="#d62728", alpha=0.35)
ax_dd.plot(drawdown.index, drawdown.values * 100, color="#d62728", linewidth=1)
ax_dd.set_title("Drawdown")
ax_dd.set_ylabel("Drawdown (%)")
ax_dd.set_xlabel("Date")
ax_dd.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig(OUTPUT_PNG, dpi=150, bbox_inches="tight")
plt.close(fig)

result = {
    "backtest_start": return_panel.index[0].strftime("%Y-%m-%d"),
    "backtest_end": nav_series.index[-1].strftime("%Y-%m-%d"),
    "aligned_trading_days": int(len(return_panel)),
    "rebalance_dates": [dt.strftime("%Y-%m-%d") for dt in rebalance_dates],
    "annualized_return_pct": round(annualized_return * 100, 4),
    "annualized_volatility_pct": round(annualized_volatility * 100, 4),
    "sharpe_ratio": round(sharpe_ratio, 6),
    "max_drawdown_pct": round(max_drawdown * 100, 4),
    "chart_path": str(OUTPUT_PNG),
}

print(json.dumps(result, ensure_ascii=False, indent=2))

summary_df = pd.DataFrame(
    [
        {
            "结果类型": "绩效摘要",
            "回测起止日": f"{result['backtest_start']}~{result['backtest_end']}",
            "共同交易日数": result["aligned_trading_days"],
            "再平衡日期": ",".join(result["rebalance_dates"]),
            "年化收益率": result["annualized_return_pct"],
            "年化波动率": result["annualized_volatility_pct"],
            "夏普比率": result["sharpe_ratio"],
            "最大回撤": result["max_drawdown_pct"],
        }
    ]
)
nav_df = nav_series.rename("组合净值").reset_index().rename(columns={"index": "日期"})
nav_df.insert(0, "结果类型", "组合净值曲线")
dd_df = (drawdown * 100).rename("回撤曲线").reset_index().rename(columns={"index": "日期"})
dd_df.insert(0, "结果类型", "回撤曲线")
__result_obj = pd.concat([summary_df, nav_df, dd_df], ignore_index=True, sort=False)


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
