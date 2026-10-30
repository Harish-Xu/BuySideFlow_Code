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


def main() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    nav_data = query_to_dataframe(sql, max_rows=0, timeout=600)
    if nav_data is None or nav_data.empty:
        raise ValueError("未取到符合条件的基金净值。")

    nav_data["trading_day"] = pd.to_datetime(nav_data["trading_day"], errors="coerce")
    nav_data["restored_nav"] = pd.to_numeric(nav_data["restored_nav"], errors="coerce")
    nav_data = nav_data.dropna(
        subset=["main_fund_code", "fund_name", "trading_day", "restored_nav"]
    )
    nav_data = nav_data[nav_data["restored_nav"] > 0]

    rows: list[dict[str, object]] = []
    for (fund_code, fund_name), group in nav_data.groupby(
        ["main_fund_code", "fund_name"], sort=False
    ):
        group = group.sort_values("trading_day")
        nav = group["restored_nav"].to_numpy(dtype=float)
        daily_returns = nav[1:] / nav[:-1] - 1
        if len(daily_returns) == 0 or not np.isfinite(daily_returns).all():
            raise ValueError(f"{fund_code} 的有效净值不足或包含无效收益率。")

        annualized_return = (nav[-1] / nav[0]) ** (250 / len(daily_returns)) - 1
        annualized_volatility = daily_returns.std(ddof=0) * np.sqrt(252)
        max_drawdown = np.min(nav / np.maximum.accumulate(nav) - 1)
        rows.append(
            {
                "主基金代码": fund_code,
                "基金简称": fund_name,
                "年化收益率": annualized_return,
                "年化波动率": annualized_volatility,
                "最大回撤": max_drawdown,
            }
        )

    result = pd.DataFrame(rows).sort_values(
        ["年化收益率", "主基金代码"], ascending=[False, True]
    )
    for column in ["年化收益率", "年化波动率", "最大回撤"]:
        result[column] = result[column].map(lambda value: f"{value * 100:.2f}")

    result = result.reset_index(drop=True)
    result.to_csv(
        Path(__file__).with_name("result.csv"), index=False, encoding="utf-8-sig"
    )
    print(result.to_string(index=False))
    return result


if __name__ == "__main__":
    main()
