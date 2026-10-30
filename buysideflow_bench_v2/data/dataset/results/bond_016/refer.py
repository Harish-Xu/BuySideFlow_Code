#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe
from sweagent.text2sql.task_output_helper import finalize_run


OBS_DATE = "2026-01-05"
NEXT_DATE = "2026-01-06"
LOOKBACK_DAYS = 90
RESULT_COLUMNS = [
    "财报期",
    "存续债只数",
    "可比债券只数",
    "资产负债率(%)",
    "货币资金/短期债务(%)",
    "经营活动现金流量净额/带息债务",
    "当前收益率中位数(%)",
    "90日变化(bp)",
]


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def sql_list(values) -> str:
    return ",".join(str(int(value)) for value in values)


def round_or_none(value, digits: int):
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)


def fetch_active_bonds() -> pd.DataFrame:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    for column in ["maincode", "innercode", "companycode", "secumarket"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["maincode", "innercode", "companycode"]).copy()
    for column in ["maincode", "innercode", "companycode"]:
        df[column] = df[column].astype("int64")
    return df.drop_duplicates(["maincode", "innercode"]).reset_index(drop=True)


def fetch_latest_financial(companycode: int) -> pd.Series | None:
    sql = f"""
    WITH ranked AS (
        SELECT
            DATE(ENDDATE) AS report_date,
            DEBTASSETSRATIO AS debt_assets_ratio_pct,
            CASHTOCURLIABILITY AS cash_to_short_debt_pct,
            NOCFTOINTERESTBEARDEBT AS ocf_to_interest_bearing_debt,
            ROW_NUMBER() OVER (
                ORDER BY
                    ENDDATE DESC,
                    INFOPUBLDATE DESC,
                    INSERTTIME DESC,
                    UPDATETIME DESC,
                    ID DESC
            ) AS rn
        FROM lc_mainindexnew
        WHERE COMPANYCODE = {int(companycode)}
          AND ENDDATE <= DATE('{OBS_DATE}')
          AND INFOPUBLDATE < DATE('{NEXT_DATE}')
    )
    SELECT
        report_date,
        debt_assets_ratio_pct,
        cash_to_short_debt_pct,
        ocf_to_interest_bearing_debt
    FROM ranked
    WHERE rn = 1
    """
    df = query_to_dataframe(sql, max_rows=1, timeout=120)
    if df.empty:
        return None
    row = df.iloc[0].copy()
    for column in [
        "debt_assets_ratio_pct",
        "cash_to_short_debt_pct",
        "ocf_to_interest_bearing_debt",
    ]:
        row[column] = pd.to_numeric(row[column], errors="coerce")
    return row


def fetch_valuation_date(active: pd.DataFrame, target_date: str) -> str:
    sql = f"""
    SELECT MAX(DATE(ENDDATE)) AS valuation_date
    FROM bond_cbvaluationall FORCE INDEX (IX_BOND_CBVALUATIONALL)
    WHERE INNERCODE IN ({sql_list(active['innercode'].tolist())})
      AND ENDDATE <= DATE('{target_date}')
      AND CREDIBILITYCODE = 1
      AND YIELDCODE = 2
      AND VPYIELD IS NOT NULL
      AND (INSERTTIME IS NULL OR INSERTTIME < TIMESTAMP('{NEXT_DATE}'))
    """
    df = query_to_dataframe(sql, max_rows=1, timeout=120)
    if df.empty or pd.isna(df.loc[0, "valuation_date"]):
        raise ValueError(f"无法取得 {target_date} 前最近估值日")
    return pd.Timestamp(df.loc[0, "valuation_date"]).strftime("%Y-%m-%d")


def fetch_yields(active: pd.DataFrame, valuation_date: str) -> pd.DataFrame:
    sql = f"""
    WITH ranked AS (
        SELECT
            CAST(INNERCODE AS UNSIGNED) AS innercode,
            VPYIELD AS valuation_yield_pct,
            INSERTTIME AS valuation_insert_time,
            UPDATETIME AS valuation_update_time,
            ID AS valuation_id,
            ROW_NUMBER() OVER (
                PARTITION BY INNERCODE
                ORDER BY INSERTTIME DESC, UPDATETIME DESC, ID DESC
            ) AS rn
        FROM bond_cbvaluationall FORCE INDEX (IX_BOND_CBVALUATIONALL)
        WHERE INNERCODE IN ({sql_list(active['innercode'].tolist())})
          AND ENDDATE >= DATE('{valuation_date}')
          AND ENDDATE < DATE_ADD(DATE('{valuation_date}'), INTERVAL 1 DAY)
          AND CREDIBILITYCODE = 1
          AND YIELDCODE = 2
          AND VPYIELD IS NOT NULL
          AND (INSERTTIME IS NULL OR INSERTTIME < TIMESTAMP('{NEXT_DATE}'))
    )
    SELECT * FROM ranked WHERE rn = 1
    """
    values = query_to_dataframe(sql, max_rows=0, timeout=600)
    if values.empty:
        return values

    values = values.copy()
    values["innercode"] = pd.to_numeric(values["innercode"], errors="coerce")
    values["valuation_yield_pct"] = pd.to_numeric(values["valuation_yield_pct"], errors="coerce")
    values = values.dropna(subset=["innercode", "valuation_yield_pct"]).copy()
    values["innercode"] = values["innercode"].astype("int64")

    panel = active.merge(values, on="innercode", how="inner")
    panel["market_priority"] = panel["secumarket"].map({89: 0, 83: 1, 90: 2}).fillna(9)
    panel = panel.sort_values(
        ["maincode", "market_priority", "bond_code", "innercode"],
        ascending=[True, True, True, True],
    )
    return panel.drop_duplicates("maincode", keep="first").reset_index(drop=True)


def build_result() -> pd.DataFrame:
    active = fetch_active_bonds()
    if active.empty:
        return empty_result()

    companycode = int(active["companycode"].mode().iloc[0])
    financial = fetch_latest_financial(companycode)
    if financial is None:
        raise ValueError("未找到观察日前最新已披露财报")

    current_date = fetch_valuation_date(active, OBS_DATE)
    prior_target = (pd.Timestamp(OBS_DATE) - pd.Timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    prior_date = fetch_valuation_date(active, prior_target)
    current = fetch_yields(active, current_date)[["maincode", "valuation_yield_pct"]]
    prior = fetch_yields(active, prior_date)[["maincode", "valuation_yield_pct"]]
    comparable = current.merge(prior, on="maincode", suffixes=("_current", "_prior"))
    if comparable.empty:
        raise ValueError("两个估值日没有共同可比债券")

    current_median = float(comparable["valuation_yield_pct_current"].median())
    prior_median = float(comparable["valuation_yield_pct_prior"].median())
    change_bp = (current_median - prior_median) * 100.0

    return pd.DataFrame(
        [
            {
                "财报期": pd.Timestamp(financial["report_date"]).strftime("%Y-%m-%d"),
                "存续债只数": int(active["maincode"].nunique()),
                "可比债券只数": int(comparable["maincode"].nunique()),
                "资产负债率(%)": round_or_none(financial["debt_assets_ratio_pct"], 2),
                "货币资金/短期债务(%)": round_or_none(financial["cash_to_short_debt_pct"], 2),
                "经营活动现金流量净额/带息债务": round_or_none(
                    financial["ocf_to_interest_bearing_debt"], 4
                ),
                "当前收益率中位数(%)": round(current_median, 4),
                "90日变化(bp)": round(change_bp, 2),
            }
        ],
        columns=RESULT_COLUMNS,
    )


def validate(df: pd.DataFrame) -> None:
    if list(df.columns) != RESULT_COLUMNS or len(df) != 1:
        raise ValueError("参考结果的行列结构不符合题面")
    row = df.iloc[0]
    if int(row["存续债只数"]) <= 0 or int(row["可比债券只数"]) <= 0:
        raise ValueError("债券样本不足")
    # 最新已披露财报的指标可缺失；保留NULL，不退回较早的完整财报。
    required = [
        "财报期",
        "当前收益率中位数(%)",
        "90日变化(bp)",
    ]
    if row[required].isna().any():
        raise ValueError("参考结果存在缺失的必需指标")


def main() -> pd.DataFrame:
    result = build_result()
    if not result.empty:
        validate(result)
    return result


if __name__ == "__main__":
    finalize_run(main())
