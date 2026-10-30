#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


RETURN_ANNUALIZATION_DAYS = 250
VOLATILITY_ANNUALIZATION_DAYS = 252
RESULT_COLUMNS = [
    "指数代码",
    "指数名称",
    "年化收益",
    "最大回撤",
    "年化波动",
    "收益回撤比",
]


def calc_perf(group: pd.DataFrame) -> dict[str, object]:
    series = group.sort_values("trade_date").copy()
    returns = series["close_price"].pct_change().dropna()
    if returns.empty:
        annual_return = np.nan
        annual_vol = np.nan
        max_drawdown = np.nan
        ratio = np.nan
    else:
        total_return = series["close_price"].iloc[-1] / series["close_price"].iloc[0] - 1.0
        annual_return = (1.0 + total_return) ** (RETURN_ANNUALIZATION_DAYS / len(returns)) - 1.0
        annual_vol = returns.std(ddof=0) * np.sqrt(VOLATILITY_ANNUALIZATION_DAYS)
        drawdown = series["close_price"] / series["close_price"].cummax() - 1.0
        max_drawdown = drawdown.min()
        ratio = np.nan if np.isclose(max_drawdown, 0.0) else annual_return / abs(max_drawdown)

    return {
        "index_code": series["index_code"].iloc[0],
        "index_name": series["index_name"].iloc[0],
        "annual_return": annual_return,
        "max_drawdown": max_drawdown,
        "annual_vol": annual_vol,
        "return_drawdown_ratio": ratio,
        "start_date": series["trade_date"].iloc[0],
        "end_date": series["trade_date"].iloc[-1],
        "start_close": series["close_price"].iloc[0],
        "end_close": series["close_price"].iloc[-1],
        "return_days": len(returns),
    }


def fmt_pct(value: object) -> str:
    if pd.isna(value):
        return ""
    return f"{float(value) * 100.0:.4f}"


def fmt_ratio(value: object) -> str:
    if pd.isna(value):
        return ""
    return f"{float(value):.4f}"


def build_result(quotes: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    data = quotes.copy()
    data["trade_date"] = pd.to_datetime(data["trade_date"], errors="coerce")
    data["close_price"] = pd.to_numeric(data["close_price"], errors="coerce")
    data = (
        data.dropna(subset=["index_code", "index_name", "trade_date", "close_price"])
        .loc[lambda x: x["close_price"] > 0]
        .sort_values(["index_code", "trade_date"])
        .drop_duplicates(["index_code", "trade_date"], keep="last")
    )
    if set(data['index_code']) != {'CBA00601', 'CBA04401'}:
        raise ValueError('Both specified bond indices are required.')
    common_dates = data.groupby('trade_date')['index_code'].nunique()
    common_dates = common_dates[common_dates == 2].index
    if pd.Timestamp('2024-12-31') not in common_dates or len(common_dates) < 2:
        raise ValueError('Missing the required base date or common index observations.')
    data = data[data['trade_date'].isin(common_dates)].copy()

    diagnostics = pd.DataFrame([calc_perf(g) for _, g in data.groupby("index_code", sort=False)])
    diagnostics = diagnostics.sort_values(
        ["return_drawdown_ratio", "index_code"],
        ascending=[False, True],
    ).reset_index(drop=True)
    result = pd.DataFrame(
        {
            "指数代码": diagnostics["index_code"],
            "指数名称": diagnostics["index_name"],
            "年化收益": diagnostics["annual_return"].map(fmt_pct),
            "最大回撤": diagnostics["max_drawdown"].map(fmt_pct),
            "年化波动": diagnostics["annual_vol"].map(fmt_pct),
            "收益回撤比": diagnostics["return_drawdown_ratio"].map(fmt_ratio),
        }
    )
    return result[RESULT_COLUMNS], diagnostics


def main() -> pd.DataFrame:
    base_dir = Path(__file__).resolve().parent
    sql = (base_dir / "refer.sql").read_text(encoding="utf-8")
    raw = query_to_dataframe(sql, max_rows=0, timeout=600)
    if raw is None:
        raise RuntimeError("SQL returned None")
    result, _ = build_result(raw)
    result.to_csv(base_dir / "result.csv", index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
