#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
import pandas as pd

SCRIPT_MODE = Path(globals().get("__file__", "")).name == "refer.py"
if SCRIPT_MODE:
    REPO_ROOT = Path(__file__).resolve().parents[4]
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

from benchmark_paths import load_benchmark_env
from sweagent.text2sql.db_connector import query_to_dataframe
from sweagent.text2sql.provenance import require_db_snapshot_id

START_DATE = "2025-02-09"
AS_OF_DATE = "2026-02-09"
OUTPUT_COLUMNS = ["基金数量", "平均收益率", "平均最大回撤"]
WINDOW_SQL = f"""
SELECT MAX(CASE WHEN TRADINGDATE <= '{START_DATE}' THEN TRADINGDATE END) AS start_day,
       MAX(TRADINGDATE) AS end_day
FROM qt_tradingdaynew
WHERE SECUMARKET IN (83, 90) AND IFTRADINGDAY = 1
  AND TRADINGDATE >= '2025-01-01' AND TRADINGDATE < '{AS_OF_DATE}'
"""
POOL_SQL = f"""
WITH eligible AS (
    SELECT f.INNERCODE, f.SECURITYCODE AS fund_code
    FROM mf_fundarchives f
    JOIN secumain s ON s.INNERCODE = f.INNERCODE AND s.SECUCATEGORY = 8
    WHERE f.MAINCODE = f.SECURITYCODE AND f.MAINCODE <> ''
      AND f.TYPE IN (2, 3, 4, 7, 8)
      AND COALESCE(f.IFFOF, 2) <> 1
      AND f.ESTABLISHMENTDATE < '{AS_OF_DATE}'
      AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE >= '{AS_OF_DATE}')
), ranked AS (
    SELECT t.INNERCODE, t.THIRDASSETCATCODE,
           ROW_NUMBER() OVER (PARTITION BY t.INNERCODE
               ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC) AS rn
    FROM mf_jyfundtype t
    JOIN eligible f ON f.INNERCODE = t.INNERCODE
    WHERE t.STANDARD = 75 AND t.EFFECTIVEDATE <= '{AS_OF_DATE}'
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > '{AS_OF_DATE}')
      AND t.INSERTTIME < '{AS_OF_DATE}'
)
SELECT f.INNERCODE, f.fund_code FROM eligible f
JOIN ranked t ON t.INNERCODE = f.INNERCODE
WHERE t.rn = 1 AND t.THIRDASSETCATCODE = 120301
ORDER BY f.fund_code
"""
NAV_SQL = """
WITH ranked AS (
    SELECT n.INNERCODE, DATE(n.TRADINGDAY) AS trading_day,
           n.UNITNVRESTORED AS nav,
           ROW_NUMBER() OVER (PARTITION BY n.INNERCODE, DATE(n.TRADINGDAY)
               ORDER BY n.INSERTTIME DESC, n.ID DESC) AS rn
    FROM mf_fundnetvalueretrans n
    WHERE n.INNERCODE IN ({codes})
      AND n.TRADINGDAY >= '{start}' AND n.TRADINGDAY <= '{end}'
      AND n.INSERTTIME < '2026-02-09'
)
SELECT INNERCODE, trading_day, nav FROM ranked
WHERE rn = 1 AND nav > 0
ORDER BY INNERCODE, trading_day
"""


def query(sql):
    data = query_to_dataframe(sql, max_rows=0, timeout=300)
    if data is None:
        raise ValueError("SQL execution returned None")
    return data


def load_window():
    data = query(WINDOW_SQL)
    if data.empty or data.iloc[0].isna().any():
        raise ValueError("Common Shanghai/Shenzhen trading endpoints unavailable")
    return tuple(pd.Timestamp(data.iloc[0][k]).strftime("%Y-%m-%d") for k in ("start_day", "end_day"))


def load_pool():
    pool = query(POOL_SQL)
    if pool.empty or pool.INNERCODE.duplicated().any() or pool.fund_code.duplicated().any():
        raise ValueError("Main-share fund universe missing or ambiguous")
    pool["INNERCODE"] = pool.INNERCODE.astype(int)
    return pool


def load_nav(pool, start, end):
    return query(NAV_SQL.format(codes=",".join(str(int(x)) for x in pool.INNERCODE), start=start, end=end))


def calculate_metrics(pool, nav, start, end):
    nav = nav.copy()
    nav["trading_day"] = pd.to_datetime(nav.trading_day)
    nav["nav"] = pd.to_numeric(nav.nav, errors="coerce")
    nav = nav[np.isfinite(nav.nav) & (nav.nav > 0)]
    nav = nav[nav.trading_day.between(pd.Timestamp(start), pd.Timestamp(end))]
    if nav.duplicated(["INNERCODE", "trading_day"]).any():
        raise ValueError("Daily NAV versions were not deduplicated")
    rows = []
    for code, group in nav.groupby("INNERCODE"):
        group = group.sort_values("trading_day")
        if group.iloc[0].trading_day != pd.Timestamp(start) or group.iloc[-1].trading_day != pd.Timestamp(end):
            continue
        values = group.nav.to_numpy(dtype=float)
        rows.append({"INNERCODE": int(code), "return_pct": (values[-1] / values[0] - 1) * 100,
                     "max_drawdown_pct": float(np.max(1 - values / np.maximum.accumulate(values)) * 100)})
    if not rows:
        raise ValueError("No funds have valid NAV at both common endpoints")
    return pool.merge(pd.DataFrame(rows), on="INNERCODE", validate="one_to_one").sort_values("fund_code").reset_index(drop=True)


def summarize(metrics):
    return pd.DataFrame([{"基金数量": int(len(metrics)),
                          "平均收益率": round(float(metrics.return_pct.mean()), 2),
                          "平均最大回撤": round(float(metrics.max_drawdown_pct.mean()), 2)}], columns=OUTPUT_COLUMNS)


def highlight_codes(metrics):
    highest_return = metrics.sort_values(["return_pct", "fund_code"], ascending=[False, True]).head(3).fund_code.tolist()
    largest_drawdown = metrics.sort_values(["max_drawdown_pct", "fund_code"], ascending=[False, True]).head(3).fund_code.tolist()
    return {"highest_return": highest_return, "largest_drawdown": largest_drawdown}


def plot_metrics(metrics, path=None):
    has_cjk = False
    for name in ["PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", "SimHei", "Arial Unicode MS"]:
        try:
            font_manager.findfont(name, fallback_to_default=False)
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            has_cjk = True
            break
        except ValueError:
            continue
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(11, 7))
    ax.scatter(metrics.max_drawdown_pct, metrics.return_pct, s=40, color="#64748b", alpha=.6, edgecolor="white")
    groups = highlight_codes(metrics)
    for key, color, marker, label in [
        ("highest_return", "#087f8c", "o", "收益率最高3只" if has_cjk else "Top 3 returns"),
        ("largest_drawdown", "#c3423f", "s", "最大回撤最大3只" if has_cjk else "Top 3 drawdowns"),
    ]:
        subset = metrics[metrics.fund_code.isin(groups[key])]
        ax.scatter(subset.max_drawdown_pct, subset.return_pct, s=65, color=color, marker=marker, label=label, zorder=3)
    codes = list(dict.fromkeys(groups["highest_return"] + groups["largest_drawdown"]))
    for i, code in enumerate(codes):
        row = metrics.loc[metrics.fund_code == code].iloc[0]
        ax.annotate(str(code), (row.max_drawdown_pct, row.return_pct),
            xytext=(10, 12 + 18 * (i % 3)), textcoords="offset points", fontsize=10,
            arrowprops={"arrowstyle": "-", "color": "#777777", "lw": .7}, zorder=4)
    ax.set_xlabel("最大回撤 (%)" if has_cjk else "Maximum drawdown (%)")
    ax.set_ylabel("区间收益率 (%)" if has_cjk else "Period return (%)")
    ax.set_title("股债平衡型主基金收益与回撤" if has_cjk else "Balanced funds: return and drawdown")
    ax.grid(alpha=.18)
    ax.margins(x=.17, y=.22)
    ax.set_xlim(left=0)
    ax.legend(loc="lower right", frameon=False)
    fig.tight_layout()
    if path is not None:
        fig.savefig(path, dpi=160, bbox_inches="tight")
        plt.close(fig)


def run_analysis(output_dir=None):
    start, end = load_window()
    pool = load_pool()
    metrics = calculate_metrics(pool, load_nav(pool, start, end), start, end)
    summary = summarize(metrics)
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        summary.to_csv(output_dir / "result.csv", index=False, encoding="utf-8-sig", float_format="%.2f")
    plot_metrics(metrics, output_dir / "picture.png" if output_dir is not None else None)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    load_benchmark_env()
    require_db_snapshot_id()
    summary = run_analysis(args.output_dir)
    print(summary.to_string(index=False))
    return summary


if __name__ == "__main__":
    result = main() if SCRIPT_MODE else run_analysis()
