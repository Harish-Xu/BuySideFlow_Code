#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = "2025-08-29"
REPORT_DATE = "2025-06-30"
NAV_LOOKBACK_DAYS = 550
OBS_WINDOW = 252
REQUIRED_INDUSTRIES = {"电子", "计算机", "通信"}

OUTPUT_COLUMNS = [
    "基金代码",
    "简称",
    "第一大行业",
    "第一大行业占比",
    "第二大行业",
    "第二大行业占比",
    "第三大行业",
    "第三大行业占比",
    "近一年年化收益",
    "近一年最大回撤",
    "Calmar",
]


def sql_in(values: pd.Series | list[int]) -> str:
    return ",".join(str(int(value)) for value in values)


def fetch_industry_exposure_summary(as_of_date: str = AS_OF_DATE) -> pd.DataFrame:
    sql = f"""
WITH fund_pool AS (
    SELECT
        CAST(fa.INNERCODE AS UNSIGNED) AS INNERCODE,
        fa.SECURITYCODE AS fund_code,
        COALESCE(sm.CHINAMEABBR, sm.SECUABBR, sm.CHINAME, sm.SECUCODE) AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
    WHERE sm.SECUCATEGORY = 8
      AND fa.INVESTMENTTYPE = 16
      AND fa.TYPE IN (2, 3, 7)
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= DATE('{as_of_date}')
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > DATE('{as_of_date}'))
),
stock_hold_dedup AS (
    SELECT
        fp.INNERCODE,
        fp.fund_code,
        fp.fund_name,
        CAST(p.STOCKINNERCODE AS UNSIGNED) AS stock_innercode,
        CAST(p.RATIOINNV AS DECIMAL(18, 8)) AS ratio_in_nv,
        ROW_NUMBER() OVER (
            PARTITION BY p.INNERCODE, p.STOCKINNERCODE
            ORDER BY
                COALESCE(p.INFOPUBLDATE, DATE('1900-01-01')) DESC,
                COALESCE(p.INSERTTIME, DATE('1900-01-01')) DESC,
                p.XGRQ DESC,
                p.ID DESC
        ) AS rn
    FROM mf_stockportfoliodetail p
    JOIN fund_pool fp
      ON fp.INNERCODE = p.INNERCODE
    WHERE p.REPORTDATE = DATE('{REPORT_DATE}')
      AND p.RATIOINNV IS NOT NULL
      AND (p.INFOPUBLDATE IS NULL OR p.INFOPUBLDATE < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY))
      AND (p.INSERTTIME IS NULL OR p.INSERTTIME < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY))
      AND p.XGRQ < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
),
holding_stock AS (
    SELECT
        sh.INNERCODE,
        sh.fund_code,
        sh.fund_name,
        sh.ratio_in_nv,
        CAST(stk.COMPANYCODE AS UNSIGNED) AS COMPANYCODE
    FROM stock_hold_dedup sh
    JOIN secumain stk
      ON stk.INNERCODE = sh.stock_innercode
    WHERE sh.rn = 1
      AND stk.SECUCATEGORY = 1
      AND stk.COMPANYCODE IS NOT NULL
),
industry_raw AS (
    SELECT
        CAST(e.COMPANYCODE AS UNSIGNED) AS COMPANYCODE,
        e.FIRSTINDUSTRYNAME,
        e.INFOPUBLDATE,
        e.INSERTTIME,
        1 AS source_priority,
        e.ID
    FROM lc_exgindustry e
    JOIN (SELECT DISTINCT COMPANYCODE FROM holding_stock) hs
      ON hs.COMPANYCODE = e.COMPANYCODE
    WHERE e.STANDARD = 37
      AND e.FIRSTINDUSTRYNAME IS NOT NULL
      AND e.INFOPUBLDATE < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
      AND (e.INSERTTIME IS NULL OR e.INSERTTIME < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY))
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > DATE('{as_of_date}'))

    UNION ALL

    SELECT
        CAST(e.COMPANYCODE AS UNSIGNED) AS COMPANYCODE,
        e.FIRSTINDUSTRYNAME,
        e.INFOPUBLDATE,
        e.INSERTTIME,
        2 AS source_priority,
        e.ID
    FROM lc_stibexgindustry e
    JOIN (SELECT DISTINCT COMPANYCODE FROM holding_stock) hs
      ON hs.COMPANYCODE = e.COMPANYCODE
    WHERE e.STANDARD = 37
      AND e.FIRSTINDUSTRYNAME IS NOT NULL
      AND e.EFFECTIVEDATE <= DATE('{as_of_date}')
      AND e.INFOPUBLDATE < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
      AND e.INSERTTIME < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > DATE('{as_of_date}'))
),
industry_ranked AS (
    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYNAME,
        ROW_NUMBER() OVER (
            PARTITION BY e.COMPANYCODE
            ORDER BY
                e.INFOPUBLDATE DESC,
                COALESCE(e.INSERTTIME, DATE('1900-01-01')) DESC,
                e.source_priority ASC,
                e.ID DESC
        ) AS rn
    FROM industry_raw e
),
industry_exposure AS (
    SELECT
        hs.INNERCODE,
        hs.fund_code,
        hs.fund_name,
        ir.FIRSTINDUSTRYNAME AS industry_name,
        SUM(hs.ratio_in_nv) AS ratio_in_nv
    FROM holding_stock hs
    JOIN industry_ranked ir
      ON ir.COMPANYCODE = hs.COMPANYCODE
     AND ir.rn = 1
    GROUP BY hs.INNERCODE, hs.fund_code, hs.fund_name, ir.FIRSTINDUSTRYNAME
),
industry_ordered AS (
    SELECT
        ie.*,
        COUNT(*) OVER (PARTITION BY ie.INNERCODE) AS industry_count,
        ROW_NUMBER() OVER (
            PARTITION BY ie.INNERCODE
            ORDER BY ie.ratio_in_nv DESC, ie.industry_name ASC
        ) AS industry_rank
    FROM industry_exposure ie
)
SELECT
    INNERCODE,
    fund_code,
    fund_name,
    MAX(industry_count) AS industry_count,
    MAX(CASE WHEN industry_rank = 1 THEN industry_name END) AS top1_industry,
    MAX(CASE WHEN industry_rank = 1 THEN ratio_in_nv END) AS top1_ratio,
    MAX(CASE WHEN industry_rank = 2 THEN industry_name END) AS top2_industry,
    MAX(CASE WHEN industry_rank = 2 THEN ratio_in_nv END) AS top2_ratio,
    MAX(CASE WHEN industry_rank = 3 THEN industry_name END) AS top3_industry,
    MAX(CASE WHEN industry_rank = 3 THEN ratio_in_nv END) AS top3_ratio,
    SUM(CASE WHEN industry_rank <= 3 THEN ratio_in_nv ELSE 0 END) AS top3_sum_ratio
FROM industry_ordered
GROUP BY INNERCODE, fund_code, fund_name
HAVING MAX(industry_count) >= 3
ORDER BY fund_code;
"""
    df = query_to_dataframe(sql, max_rows=0, timeout=900)
    if df is None or df.empty:
        raise ValueError("未取得观察日前已披露的 2025 年半年报行业暴露")

    df = df.copy()
    numeric_columns = [
        "INNERCODE",
        "industry_count",
        "top1_ratio",
        "top2_ratio",
        "top3_ratio",
        "top3_sum_ratio",
    ]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=numeric_columns + ["top1_industry", "top2_industry", "top3_industry"]).copy()
    df["INNERCODE"] = df["INNERCODE"].astype("int64")
    df["industry_count"] = df["industry_count"].astype(int)
    return df.reset_index(drop=True)


def select_exposure_candidates(summary: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    def apply_rule(min_count: int, max_count: int, threshold: float) -> pd.DataFrame:
        top_names = summary[["top1_industry", "top2_industry", "top3_industry"]].apply(
            lambda row: set(row), axis=1
        )
        mask = (
            summary["industry_count"].between(min_count, max_count)
            & top_names.apply(lambda names: REQUIRED_INDUSTRIES.issubset(names))
            & (summary["top1_ratio"] >= threshold)
            & (summary["top2_ratio"] >= threshold)
            & (summary["top3_ratio"] >= threshold)
        )
        return summary.loc[mask].copy()

    primary = apply_rule(min_count=5, max_count=10, threshold=0.10)
    if len(primary) >= 5:
        return primary, "primary"
    return apply_rule(min_count=5, max_count=12, threshold=0.08), "relaxed"


def fetch_nav(candidates: pd.DataFrame, as_of_date: str = AS_OF_DATE) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame(columns=["INNERCODE", "trading_day", "restored_nav"])

    sql = f"""
WITH nav_ranked AS (
    SELECT
        CAST(n.INNERCODE AS UNSIGNED) AS INNERCODE,
        DATE(n.TRADINGDAY) AS trading_day,
        n.UNITNVRESTORED AS restored_nav,
        ROW_NUMBER() OVER (
            PARTITION BY n.INNERCODE, DATE(n.TRADINGDAY)
            ORDER BY n.UPDATETIME DESC, n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans n
    WHERE n.INNERCODE IN ({sql_in(candidates["INNERCODE"])})
      AND n.UNITNVRESTORED IS NOT NULL
      AND n.TRADINGDAY >= DATE_SUB(DATE('{as_of_date}'), INTERVAL {NAV_LOOKBACK_DAYS} DAY)
      AND n.TRADINGDAY < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
      AND n.INSERTTIME < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
      AND n.UPDATETIME < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
)
SELECT
    INNERCODE,
    trading_day,
    restored_nav
FROM nav_ranked
WHERE rn = 1
ORDER BY INNERCODE, trading_day;
"""
    df = query_to_dataframe(sql, max_rows=0, timeout=900)
    if df is None or df.empty:
        return pd.DataFrame(columns=["INNERCODE", "trading_day", "restored_nav"])

    df = df.copy()
    df["INNERCODE"] = pd.to_numeric(df["INNERCODE"], errors="coerce")
    df["trading_day"] = pd.to_datetime(df["trading_day"], errors="coerce")
    df["restored_nav"] = pd.to_numeric(df["restored_nav"], errors="coerce")
    df = df.dropna(subset=["INNERCODE", "trading_day", "restored_nav"]).copy()
    df["INNERCODE"] = df["INNERCODE"].astype("int64")
    return df.reset_index(drop=True)


def compute_nav_metrics(nav: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, float | int]] = []
    for innercode, group in nav.groupby("INNERCODE"):
        group = group.sort_values("trading_day").tail(OBS_WINDOW).reset_index(drop=True)
        if len(group) < OBS_WINDOW:
            continue

        first_nav = float(group.iloc[0]["restored_nav"])
        last_nav = float(group.iloc[-1]["restored_nav"])
        if first_nav <= 0 or last_nav <= 0:
            continue

        annualized_return = (last_nav / first_nav) ** (250.0 / (len(group) - 1)) - 1.0
        running_peak = group["restored_nav"].cummax()
        max_drawdown = (group["restored_nav"] / running_peak - 1.0).min()
        if pd.isna(max_drawdown) or max_drawdown >= 0:
            continue

        rows.append(
            {
                "INNERCODE": int(innercode),
                "nav_obs_count": int(len(group)),
                "nav_start_date": group.iloc[0]["trading_day"],
                "nav_end_date": group.iloc[-1]["trading_day"],
                "nav_first": first_nav,
                "nav_last": last_nav,
                "annualized_return": float(annualized_return),
                "max_drawdown": float(max_drawdown),
                "calmar": float(annualized_return / abs(max_drawdown)),
            }
        )

    return pd.DataFrame(rows)


def build_output(final: pd.DataFrame) -> pd.DataFrame:
    if final.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    output = final[
        [
            "fund_code",
            "fund_name",
            "top1_industry",
            "top1_ratio",
            "top2_industry",
            "top2_ratio",
            "top3_industry",
            "top3_ratio",
            "annualized_return",
            "max_drawdown",
            "calmar",
        ]
    ].copy()

    for column in ["top1_ratio", "top2_ratio", "top3_ratio", "annualized_return", "max_drawdown"]:
        output[column] = (output[column] * 100).map(lambda value: f"{value:.2f}")
    output["calmar"] = output["calmar"].map(lambda value: f"{value:.4f}")
    output.columns = OUTPUT_COLUMNS
    return output


def main() -> pd.DataFrame:
    output_path = Path(__file__).with_name("result.csv")

    exposure = fetch_industry_exposure_summary()
    candidates, _ = select_exposure_candidates(exposure)
    nav = fetch_nav(candidates)
    metrics = compute_nav_metrics(nav)
    final = (
        candidates.merge(metrics, on="INNERCODE", how="inner")
        .sort_values(["calmar", "annualized_return", "top3_sum_ratio", "fund_code"], ascending=[False, False, False, True])
        .head(5)
        .reset_index(drop=True)
    )
    output = build_output(final)
    output.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(output.to_string(index=False) if not output.empty else "<empty result>")
    return output


if __name__ == "__main__":
    main()
