#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = "2026-02-28"
YTD_START_CUTOFF = "2025-12-31"
YEAR_START = "2026-01-01"
FEB_START = "2026-02-01"
HSTECH_CODE = 1270663
MIN_WEIGHT = 0.8

OUTPUT_COLUMNS = [
    "基金代码",
    "基金简称",
    "恒生科技基准权重下限",
    "2月首次创新高日期",
    "今年以来超额收益",
]


def sql_in(values: pd.Series | list[int]) -> str:
    return ",".join(str(int(value)) for value in values)


def fetch_candidate_funds(as_of_date: str = AS_OF_DATE) -> pd.DataFrame:
    sql = f"""
WITH ranked_benchmark AS (
    SELECT
        ba.INNERCODE,
        fa.MAINCODE AS fund_code,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, sm.SECUCODE) AS fund_name,
        ba.MINIMUMWEIGHT AS hstech_min_weight,
        ROW_NUMBER() OVER (
            PARTITION BY ba.INNERCODE
            ORDER BY ba.EXCUTEDATE DESC, ba.INFOPUBLDATE DESC, ba.UPDATETIME DESC, ba.INSERTTIME DESC, ba.ID DESC
        ) AS rn
    FROM mf_benchmarkallocation ba
    JOIN secumain sm
      ON sm.INNERCODE = ba.INNERCODE
    JOIN mf_fundarchives fa
      ON fa.INNERCODE = ba.INNERCODE
    WHERE ba.TRACEDINDEXCODE = {HSTECH_CODE}
      AND ba.EXCUTEDATE < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
      AND (ba.CANCELDATE IS NULL OR ba.CANCELDATE > DATE('{as_of_date}'))
      AND COALESCE(ba.INFOPUBLDATE, ba.INSERTTIME) < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
      AND sm.SECUCATEGORY = 8
      AND fa.MAINCODE = fa.SECURITYCODE
      AND (fa.ESTABLISHMENTDATE IS NULL OR fa.ESTABLISHMENTDATE < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY))
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > DATE('{as_of_date}'))
)
SELECT
    INNERCODE,
    fund_code,
    fund_name,
    hstech_min_weight
FROM ranked_benchmark
WHERE rn = 1
  AND hstech_min_weight >= {MIN_WEIGHT}
ORDER BY fund_code;
"""
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        raise ValueError("未找到观察日有效、恒生科技基准占比至少 80% 的基金")

    df = df.copy()
    df["INNERCODE"] = pd.to_numeric(df["INNERCODE"], errors="coerce")
    df["hstech_min_weight"] = pd.to_numeric(df["hstech_min_weight"], errors="coerce")
    df = df.dropna(subset=["INNERCODE", "hstech_min_weight"]).copy()
    df["INNERCODE"] = df["INNERCODE"].astype("int64")
    if df.empty:
        raise ValueError("候选基金基准权重数据为空")
    return df.reset_index(drop=True)


def fetch_fund_nav(funds: pd.DataFrame, as_of_date: str = AS_OF_DATE) -> pd.DataFrame:
    sql = f"""
WITH nav_ranked AS (
    SELECT
        INNERCODE,
        DATE(TRADINGDAY) AS trade_date,
        UNITNVRESTORED,
        ROW_NUMBER() OVER (
            PARTITION BY INNERCODE, DATE(TRADINGDAY)
            ORDER BY UPDATETIME DESC, INSERTTIME DESC, ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans
    WHERE INNERCODE IN ({sql_in(funds["INNERCODE"])})
      AND TRADINGDAY >= DATE('{YTD_START_CUTOFF}')
      AND TRADINGDAY < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
      AND INSERTTIME < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
)
SELECT
    INNERCODE,
    trade_date,
    UNITNVRESTORED AS restored_nav
FROM nav_ranked
WHERE rn = 1 AND UNITNVRESTORED > 0
ORDER BY INNERCODE, trade_date;
"""
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        raise ValueError("候选基金净值为空，无法判断创新高和收益表现")

    df = df.copy()
    df["INNERCODE"] = pd.to_numeric(df["INNERCODE"], errors="coerce")
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df["restored_nav"] = pd.to_numeric(df["restored_nav"], errors="coerce")
    df = df.dropna(subset=["INNERCODE", "trade_date", "restored_nav"]).copy()
    df["INNERCODE"] = df["INNERCODE"].astype("int64")
    return df.reset_index(drop=True)


def fetch_hstech_prices(as_of_date: str = AS_OF_DATE) -> pd.DataFrame:
    sql = f"""
WITH quote_ranked AS (
    SELECT
        DATE(TRADINGDAY) AS trade_date,
        CLOSEPRICE,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(TRADINGDAY)
            ORDER BY UPDATETIME DESC, INSERTTIME DESC, ID DESC
        ) AS rn
    FROM qt_osindexquote
    WHERE INDEXCODE = {HSTECH_CODE}
      AND TRADINGDAY >= DATE('{YTD_START_CUTOFF}')
      AND TRADINGDAY < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
      AND (INSERTTIME IS NULL OR INSERTTIME < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY))
)
SELECT trade_date, CLOSEPRICE AS close_price
FROM quote_ranked
WHERE rn = 1 AND CLOSEPRICE > 0
ORDER BY trade_date;
"""
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        raise ValueError("未查询到恒生科技指数行情数据")

    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df["close_price"] = pd.to_numeric(df["close_price"], errors="coerce")
    df = df.dropna(subset=["trade_date", "close_price"]).sort_values("trade_date")

    return df


def calculate_fund_metrics(
    funds: pd.DataFrame,
    nav: pd.DataFrame,
    hstech_prices: pd.DataFrame,
    as_of_date: str = AS_OF_DATE,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for innercode, group in nav.groupby("INNERCODE"):
        group = group.sort_values("trade_date").reset_index(drop=True)
        aligned = group.merge(hstech_prices, on="trade_date", validate="one_to_one")
        aligned = aligned.sort_values("trade_date")
        ytd_start = aligned[aligned["trade_date"] == pd.Timestamp(YTD_START_CUTOFF)]
        ytd_end = aligned[aligned["trade_date"] <= pd.Timestamp(as_of_date)]
        if ytd_start.empty or ytd_end.empty:
            continue

        start_nav = float(ytd_start.iloc[-1]["restored_nav"])
        end_nav = float(ytd_end.iloc[-1]["restored_nav"])
        if start_nav <= 0:
            continue
        fund_ytd_return = end_nav / start_nav - 1.0
        hstech_ytd_return = float(ytd_end.iloc[-1]["close_price"]) / float(ytd_start.iloc[0]["close_price"]) - 1.0

        new_high_date = None
        ytd_window = group[
            (group["trade_date"] >= pd.Timestamp(YEAR_START))
            & (group["trade_date"] <= pd.Timestamp(as_of_date))
        ].copy()
        feb = ytd_window[ytd_window["trade_date"] >= pd.Timestamp(FEB_START)]
        for _, row in feb.iterrows():
            prior = ytd_window[ytd_window["trade_date"] < row["trade_date"]]
            if not prior.empty and float(row["restored_nav"]) > float(prior["restored_nav"].max()):
                new_high_date = row["trade_date"]
                break

        rows.append(
            {
                "INNERCODE": int(innercode),
                "fund_ytd_return": fund_ytd_return,
                "excess_return": fund_ytd_return - hstech_ytd_return,
                "new_high_date": new_high_date,
            }
        )

    metrics = pd.DataFrame(rows)
    if metrics.empty:
        return metrics

    result = funds.merge(metrics, on="INNERCODE", how="inner")
    result = result[result["excess_return"] > 0].copy()
    if result.empty:
        return result

    result["new_high_date"] = pd.to_datetime(result["new_high_date"], errors="coerce")
    return result.sort_values(
        ["excess_return", "fund_code"],
        ascending=[False, True],
        na_position="last",
    ).reset_index(drop=True)


def build_output(final: pd.DataFrame) -> pd.DataFrame:
    if final.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    output = final[
        ["fund_code", "fund_name", "hstech_min_weight", "new_high_date", "excess_return"]
    ].copy()
    output["new_high_date"] = pd.to_datetime(
        output["new_high_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    output["new_high_date"] = output["new_high_date"].fillna("")
    output["hstech_min_weight"] = (output["hstech_min_weight"] * 100).round(2)
    output["excess_return"] = (output["excess_return"] * 100).round(2)
    output.columns = OUTPUT_COLUMNS
    return output


def main() -> pd.DataFrame:
    output_path = Path(__file__).with_name("result.csv")

    funds = fetch_candidate_funds()
    nav = fetch_fund_nav(funds)
    hstech_prices = fetch_hstech_prices()
    final = calculate_fund_metrics(funds, nav, hstech_prices)
    output = build_output(final)

    output.to_csv(output_path, index=False, encoding="utf-8-sig", float_format="%.2f")
    print(output.to_string(index=False) if not output.empty else "<empty result>")
    return output


if __name__ == "__main__":
    main()
