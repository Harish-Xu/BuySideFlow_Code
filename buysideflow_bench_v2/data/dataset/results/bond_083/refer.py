#!/usr/bin/env python3
from __future__ import annotations

import sys
from bisect import bisect_left
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from statistics import median

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = "2025-05-31"
NEXT_DATE = "2025-06-01"
MIN_BOND_COUNT = 3
MAX_YIELD_PERCENTILE = 20.0
CHUNK_SIZE = 1200
CREDIT_L1 = (1600, 1700, 1800, 2500, 2600, 2800, 2900, 3100, 3200, 3300, 3700)
CREDIT_L2 = (1901, 1903)

RESULT_COLUMNS = [
    "主体名称",
    "主体评级",
    "财报期",
    "有估值存续债只数",
    "现金流比率",
    "应收账款与存货合计同比增速（%）",
    "营业收入同比增速（%）",
    "估值收益率中位数（%）",
    "组内分位（%）",
]

# JY domestic grade codes increase with credit quality, irrespective of pi/spc labels.
RATING_GRADES = {
    740: "D",
    760: "C",
    770: "CC",
    799: "CCC",
    830: "B-",
    840: "B",
    850: "B+",
    860: "BB-",
    870: "BB",
    880: "BB+",
    891: "BBB-",
    895: "BBB",
    899: "BBB+",
    930: "A-",
    940: "A",
    950: "A+",
    960: "AA-",
    970: "AA",
    980: "AA+",
    990: "AAA-",
    999: "AAA",
    1000: "AAA+",
}


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=RESULT_COLUMNS)


def chunks(values, size=CHUNK_SIZE):
    values = sorted({int(v) for v in values if pd.notna(v)})
    for i in range(0, len(values), size):
        yield values[i : i + size]


def sql_list(values) -> str:
    return ",".join(str(int(v)) for v in values)


def decimal_value(value: object) -> Decimal | float:
    return Decimal(str(value)) if pd.notna(value) else np.nan


def first_valid(values: pd.Series) -> object:
    valid = values.dropna()
    if valid.empty:
        return np.nan
    return valid.iloc[0]


def latest_valuation_date() -> str | None:
    sql = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        return None
    date_value = pd.to_datetime(df.loc[0, "valuation_date"], errors="coerce")
    if pd.isna(date_value):
        return None
    return date_value.strftime("%Y-%m-%d")


def fetch_live_credit_bond_codes() -> pd.DataFrame:
    sql = f"""
    SELECT
        CAST(MAINCODE AS UNSIGNED) AS maincode,
        CAST(INNERCODE AS UNSIGNED) AS innercode,
        CAST(COMPANYCODE AS UNSIGNED) AS companycode,
        SECUCODE AS bond_code,
        SECUABBR AS bond_name,
        ISSUER AS issuer,
        SECUMARKET AS secumarket
    FROM bond_code
    WHERE MAINCODE IS NOT NULL
      AND INNERCODE IS NOT NULL
      AND COMPANYCODE IS NOT NULL
      AND SECUMARKET = 89
      AND (BONDTYPELEVEL1 IN ({sql_list(CREDIT_L1)}) OR BONDTYPELEVEL2 IN ({sql_list(CREDIT_L2)}))
      AND (LISTEDDATE IS NULL OR DATE(LISTEDDATE) <= DATE('{AS_OF_DATE}'))
      AND (INTERESTENDDATE IS NULL OR DATE(INTERESTENDDATE) >= DATE('{AS_OF_DATE}'))
      AND (DELISTDATE IS NULL OR DATE(DELISTDATE) >= DATE('{AS_OF_DATE}'))
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    for col in ["maincode", "innercode", "companycode", "secumarket"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["maincode", "innercode", "companycode"]).copy()
    if df.empty:
        return df

    for col in ["maincode", "innercode", "companycode"]:
        df[col] = df[col].astype("int64")
    return df.drop_duplicates(["maincode", "innercode"], keep="first").reset_index(drop=True)


def fetch_recommended_valuations(valuation_date: str, innercodes: list[int]) -> pd.DataFrame:
    if not innercodes:
        return pd.DataFrame()
    sql = f"""
        SELECT
            CAST(INNERCODE AS UNSIGNED) AS innercode,
            DATE(ENDDATE) AS valuation_date,
            CAST(VPYIELD AS CHAR) AS valuation_yield_pct,
            CREDIBILITYCODE,
            YIELDCODE,
            INSERTTIME,
            UPDATETIME,
            ID
        FROM bond_cbvaluationall FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
        WHERE INNERCODE IN ({sql_list(innercodes)})
          AND ENDDATE >= DATE('{valuation_date}')
          AND ENDDATE < DATE_ADD(DATE('{valuation_date}'), INTERVAL 1 DAY)
          AND CREDIBILITYCODE = 1
          AND YIELDCODE = 2
          AND VPYIELD IS NOT NULL
          AND INSERTTIME < TIMESTAMP('{NEXT_DATE}')
        """
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    for col in ["innercode", "CREDIBILITYCODE", "YIELDCODE", "ID"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # Text transport avoids the shared connector's Decimal-to-float conversion.
    df["valuation_yield_pct"] = df["valuation_yield_pct"].map(decimal_value)
    df["valuation_date"] = pd.to_datetime(df["valuation_date"], errors="coerce")
    df["INSERTTIME"] = pd.to_datetime(df["INSERTTIME"], errors="coerce")
    df["UPDATETIME"] = pd.to_datetime(df["UPDATETIME"], errors="coerce")
    df = df.dropna(subset=["innercode", "valuation_date", "valuation_yield_pct"]).copy()
    if df.empty:
        return df
    df["innercode"] = df["innercode"].astype("int64")
    df = df.sort_values(
        ["innercode", "INSERTTIME", "UPDATETIME", "ID"],
        ascending=[True, False, False, False],
    )
    return df.drop_duplicates("innercode", keep="first").reset_index(drop=True)


def load_bond_valuations() -> pd.DataFrame:
    valuation_date = latest_valuation_date()
    if valuation_date is None:
        return pd.DataFrame()

    code_map = fetch_live_credit_bond_codes()
    if code_map.empty:
        return pd.DataFrame()

    vals = fetch_recommended_valuations(valuation_date, code_map["innercode"].tolist())
    if vals.empty:
        return pd.DataFrame()

    df = code_map.merge(vals, on="innercode", how="inner")
    if df.empty:
        return df

    df = df.sort_values(
        ["maincode", "bond_code", "innercode", "UPDATETIME", "ID"],
        ascending=[True, True, True, False, False],
    )
    return df.drop_duplicates("maincode", keep="first").reset_index(drop=True)


def build_issuer_yield_panel(bonds: pd.DataFrame) -> pd.DataFrame:
    if bonds.empty:
        return pd.DataFrame()

    issuer_panel = (
        bonds.groupby("companycode", as_index=False)
        .agg(
            issuer=("issuer", first_valid),
            live_credit_bond_count=("maincode", "nunique"),
            issuer_median_yield_pct=("valuation_yield_pct", median),
        )
    )
    issuer_panel = issuer_panel[issuer_panel["live_credit_bond_count"] >= MIN_BOND_COUNT].copy()
    issuer_panel["companycode"] = issuer_panel["companycode"].astype("int64")
    return issuer_panel.reset_index(drop=True)


def fetch_subject_ratings(companycodes: list[int]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for part in chunks(companycodes):
        sql = f"""
        SELECT
            CAST(COMPANYCODE AS UNSIGNED) AS companycode,
            CAST(CRASCODE AS UNSIGNED) AS agency_code,
            CRCODE AS rating_code,
            CRDATE AS rating_date,
            INFOPUBLDATE AS rating_publish_date,
            INSERTTIME AS rating_insert_time,
            UPDATETIME AS rating_update_time,
            ID
        FROM bond_comcreditgrading FORCE INDEX (IX_BOND_COMCREDITGRADING)
        WHERE COMPANYCODE IN ({sql_list(part)})
          AND CRSYSTEM = 1
          AND CRTYPE = 3
          AND INFOPUBLDATE IS NOT NULL
          AND INFOPUBLDATE < TIMESTAMP('{NEXT_DATE}')
          AND CRDATE < DATE('{NEXT_DATE}')
          AND (RATINGEFFECTIVEENDDATE IS NULL OR DATE(RATINGEFFECTIVEENDDATE) >= DATE('{AS_OF_DATE}'))
          AND CRSTATUS = 1
        """
        frames.append(query_to_dataframe(sql, max_rows=0, timeout=600))

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if df.empty:
        return df

    df = df.copy()
    df["companycode"] = pd.to_numeric(df["companycode"], errors="coerce")
    df["agency_code"] = pd.to_numeric(df["agency_code"], errors="coerce")
    df["rating_code"] = pd.to_numeric(df["rating_code"], errors="coerce")
    df["ID"] = pd.to_numeric(df["ID"], errors="coerce")
    df["rating_date"] = pd.to_datetime(df["rating_date"], errors="coerce")
    df["rating_publish_date"] = pd.to_datetime(df["rating_publish_date"], errors="coerce")
    df["rating_insert_time"] = pd.to_datetime(df["rating_insert_time"], errors="coerce")
    df["rating_update_time"] = pd.to_datetime(df["rating_update_time"], errors="coerce")
    df = df.dropna(subset=["companycode", "agency_code"]).copy()
    if df.empty:
        return df

    df["companycode"] = df["companycode"].astype("int64")
    df["agency_code"] = df["agency_code"].astype("int64")
    df = df.sort_values(
        [
            "companycode",
            "agency_code",
            "rating_date",
            "rating_publish_date",
            "rating_insert_time",
            "rating_update_time",
            "ID",
        ],
        ascending=[True, True, False, False, False, False, False],
    )
    latest = df.drop_duplicates(["companycode", "agency_code"], keep="first").copy()
    latest["subject_rating"] = latest["rating_code"].map(RATING_GRADES)
    latest = latest.dropna(subset=["subject_rating"]).copy()
    latest = latest.sort_values(
        ["companycode", "rating_code", "agency_code"],
        ascending=[True, True, True],
    )
    return latest.drop_duplicates("companycode", keep="first")[
        ["companycode", "subject_rating"]
    ].reset_index(drop=True)


def fetch_sw_industries(companycodes: list[int]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for part in chunks(companycodes):
        sql = f"""
        SELECT
            CAST(COMPANYCODE AS UNSIGNED) AS companycode,
            FIRSTINDUSTRYNAME AS sw_l1_industry,
            DATE(EFFECTIVEDATE) AS effective_date,
            INFOPUBLDATE AS publish_date,
            ID
        FROM bond_biindustry
        WHERE COMPANYCODE IN ({sql_list(part)})
          AND STANDARD = 38
          AND EFFECTIVEDATE < DATE('{NEXT_DATE}')
          AND INFOPUBLDATE < TIMESTAMP('{NEXT_DATE}')
          AND (CANCELDATE IS NULL OR DATE(CANCELDATE) > DATE('{AS_OF_DATE}'))
        """
        frames.append(query_to_dataframe(sql, max_rows=0, timeout=600))

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if df.empty:
        return df

    df = df.copy()
    df["companycode"] = pd.to_numeric(df["companycode"], errors="coerce")
    df["ID"] = pd.to_numeric(df["ID"], errors="coerce")
    df["effective_date"] = pd.to_datetime(df["effective_date"], errors="coerce")
    df["publish_date"] = pd.to_datetime(df["publish_date"], errors="coerce")
    df = df.dropna(subset=["companycode"]).copy()
    if df.empty:
        return df

    df["companycode"] = df["companycode"].astype("int64")
    df = df.sort_values(
        ["companycode", "effective_date", "publish_date", "ID"],
        ascending=[True, False, False, False],
    )
    latest = df.drop_duplicates("companycode", keep="first")
    return latest.loc[
        latest["sw_l1_industry"] == "建筑装饰", ["companycode", "sw_l1_industry"]
    ].reset_index(drop=True)


def fetch_main_financial_rows(companycodes: list[int]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for part in chunks(companycodes):
        sql = f"""
        SELECT
            CAST(COMPANYCODE AS UNSIGNED) AS companycode,
            DATE(ENDDATE) AS fin_enddate,
            INFOPUBLDATE AS publish_date,
            MARK AS report_mark,
            CAST(NETOPERATECASHFLOW AS CHAR) AS operating_cash_flow,
            CAST(OPERATINGREENUE AS CHAR) AS operating_revenue,
            CAST(ACCOUNTRECEIVABLES AS CHAR) AS accounts_receivable,
            CAST(INVENTORIES AS CHAR) AS inventories,
            SHORTTERMLOAN AS short_term_loan,
            NONCURRENTLIABILITYIN1YEAR AS noncurrent_liability_in_1y,
            UPDATETIME AS update_time,
            ID
        FROM lc_maindatanew
        WHERE COMPANYCODE IN ({sql_list(part)})
          AND INFOPUBLDATE IS NOT NULL
          AND INFOPUBLDATE < TIMESTAMP('{NEXT_DATE}')
          AND MARK IN (1, 8, 7, 6, 2)
          AND COALESCE(ACCOUNTINGSTANDARDS, 1) = 1
          AND ENDDATE <= DATE('{AS_OF_DATE}')
          AND ENDDATE >= DATE_SUB(DATE('{AS_OF_DATE}'), INTERVAL 4 YEAR)
        """
        frames.append(query_to_dataframe(sql, max_rows=0, timeout=600))

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if df.empty:
        return df

    df = df.copy()
    numeric_cols = [
        "companycode",
        "short_term_loan",
        "noncurrent_liability_in_1y",
        "report_mark",
        "ID",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["operating_cash_flow", "operating_revenue", "accounts_receivable", "inventories"]:
        df[col] = df[col].map(decimal_value)
    df["fin_enddate"] = pd.to_datetime(df["fin_enddate"], errors="coerce")
    df["publish_date"] = pd.to_datetime(df["publish_date"], errors="coerce")
    df["update_time"] = pd.to_datetime(df["update_time"], errors="coerce")
    df = df.dropna(subset=["companycode", "fin_enddate"]).copy()
    if df.empty:
        return df

    df["companycode"] = df["companycode"].astype("int64")
    df["mark_priority"] = df["report_mark"].map({1: 1, 8: 2, 7: 3, 6: 4, 2: 5})
    df = df.sort_values(
        ["companycode", "fin_enddate", "mark_priority", "publish_date", "update_time", "ID"],
        ascending=[True, True, True, False, False, False],
    )
    return df.drop_duplicates(["companycode", "fin_enddate"], keep="first").reset_index(drop=True)


def fetch_balance_data(companycodes: list[int]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for part in chunks(companycodes):
        sql = f"""
        SELECT
            CAST(COMPANYCODE AS UNSIGNED) AS companycode,
            DATE(ENDDATE) AS fin_enddate,
            INFOPUBLDATE AS publish_date,
            IFADJUSTED AS adjustment_mark,
            CAST(ACCOUNTRECEIVABLE AS CHAR) AS bs_accounts_receivable,
            CAST(INVENTORIES AS CHAR) AS bs_inventories,
            CAST(SHORTTERMLOAN AS CHAR) AS bs_short_term_loan,
            CAST(NONCURRENTLIABILITYIN1YEAR AS CHAR) AS bs_noncurrent_liability_in_1y,
            UPDATETIME AS update_time,
            ID
        FROM lc_balancesheetall
        WHERE COMPANYCODE IN ({sql_list(part)})
          AND IFMERGED = 1
          AND INFOPUBLDATE < TIMESTAMP('{NEXT_DATE}')
          AND IFADJUSTED IN (1, 8, 7, 6, 2)
          AND ACCOUNTINGSTANDARDS = 1
          AND ENDDATE <= DATE('{AS_OF_DATE}')
          AND ENDDATE >= DATE_SUB(DATE('{AS_OF_DATE}'), INTERVAL 4 YEAR)
        """
        frames.append(query_to_dataframe(sql, max_rows=0, timeout=600))

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if df.empty:
        return df

    df = df.copy()
    numeric_cols = [
        "companycode",
        "adjustment_mark",
        "ID",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["bs_accounts_receivable", "bs_inventories", "bs_short_term_loan", "bs_noncurrent_liability_in_1y"]:
        df[col] = df[col].map(decimal_value)
    df["fin_enddate"] = pd.to_datetime(df["fin_enddate"], errors="coerce")
    df["publish_date"] = pd.to_datetime(df["publish_date"], errors="coerce")
    df["update_time"] = pd.to_datetime(df["update_time"], errors="coerce")
    df = df.dropna(subset=["companycode", "fin_enddate"]).copy()
    if df.empty:
        return df

    df["companycode"] = df["companycode"].astype("int64")
    df["adjustment_priority"] = df["adjustment_mark"].map(
        {1: 1, 8: 2, 7: 3, 6: 4, 2: 5}
    )
    df = df.sort_values(
        [
            "companycode",
            "fin_enddate",
            "adjustment_priority",
            "publish_date",
            "update_time",
            "ID",
        ],
        ascending=[True, True, True, False, False, False],
    )
    df = df.drop_duplicates(["companycode", "fin_enddate"], keep="first").reset_index(drop=True)
    debt_cols = [
        "bs_short_term_loan",
        "bs_noncurrent_liability_in_1y",
    ]
    df["short_debt_balance_sheet"] = df[debt_cols].sum(axis=1, min_count=len(debt_cols))
    return df[
        [
            "companycode",
            "fin_enddate",
            "short_debt_balance_sheet",
            "bs_accounts_receivable",
            "bs_inventories",
        ]
    ]


def build_financial_panel(companycodes: list[int]) -> pd.DataFrame:
    main_rows = fetch_main_financial_rows(companycodes)
    if main_rows.empty:
        return pd.DataFrame()

    current = (
        main_rows.sort_values(
            ["companycode", "fin_enddate", "publish_date", "update_time", "ID"],
            ascending=[True, False, False, False, False],
        )
        .drop_duplicates("companycode", keep="first")
        .reset_index(drop=True)
    )
    current["prior_fin_enddate"] = current["fin_enddate"] - pd.DateOffset(years=1)

    prior_cols = [
        "companycode",
        "fin_enddate",
        "operating_revenue",
        "accounts_receivable",
        "inventories",
    ]
    prior = main_rows[prior_cols].rename(
        columns={
            "fin_enddate": "prior_fin_enddate",
            "operating_revenue": "prior_operating_revenue",
            "accounts_receivable": "prior_accounts_receivable",
            "inventories": "prior_inventories",
        }
    )
    financials = current.merge(prior, on=["companycode", "prior_fin_enddate"], how="left")

    balance = fetch_balance_data(companycodes)
    if not balance.empty:
        financials = financials.merge(balance, on=["companycode", "fin_enddate"], how="left")
        prior_balance = balance.rename(
            columns={
                "fin_enddate": "prior_fin_enddate",
                "short_debt_balance_sheet": "prior_short_debt_balance_sheet",
                "bs_accounts_receivable": "prior_bs_accounts_receivable",
                "bs_inventories": "prior_bs_inventories",
            }
        )
        financials = financials.merge(
            prior_balance[
                [
                    "companycode",
                    "prior_fin_enddate",
                    "prior_short_debt_balance_sheet",
                    "prior_bs_accounts_receivable",
                    "prior_bs_inventories",
                ]
            ],
            on=["companycode", "prior_fin_enddate"],
            how="left",
        )
    else:
        financials["short_debt_balance_sheet"] = np.nan
        financials["bs_accounts_receivable"] = np.nan
        financials["bs_inventories"] = np.nan
        financials["prior_bs_accounts_receivable"] = np.nan
        financials["prior_bs_inventories"] = np.nan

    current_receivables = financials["bs_accounts_receivable"]
    prior_receivables = financials["prior_bs_accounts_receivable"]
    current_inventories = financials["bs_inventories"].combine_first(financials["inventories"])
    prior_inventories = financials["prior_bs_inventories"].combine_first(
        financials["prior_inventories"]
    )
    complete_working_capital = (
        current_receivables.notna()
        & current_inventories.notna()
        & prior_receivables.notna()
        & prior_inventories.notna()
    )
    financials["receivable_inventory"] = current_receivables + current_inventories
    financials["prior_receivable_inventory"] = prior_receivables + prior_inventories
    financials["short_debt"] = financials["short_debt_balance_sheet"]
    financials = financials[
        complete_working_capital
        & financials["operating_cash_flow"].notna()
        & financials["operating_revenue"].notna()
        & (financials["short_debt"] > 0)
        & (financials["prior_receivable_inventory"] > 0)
        & (financials["prior_operating_revenue"] > 0)
    ].copy()
    # Exact ratios preserve inclusive thresholds even for repeating decimals.
    financials["receivable_inventory_yoy_pct"] = (
        financials["receivable_inventory"].map(Fraction)
        / financials["prior_receivable_inventory"].map(Fraction) - 1
    ) * 100
    financials["revenue_yoy_pct"] = (
        financials["operating_revenue"].map(Fraction)
        / financials["prior_operating_revenue"].map(Fraction) - 1
    ) * 100
    financials["cfo_short_debt_ratio"] = (
        financials["operating_cash_flow"].map(Fraction) / financials["short_debt"].map(Fraction)
    )

    keep = [
        "companycode",
        "fin_enddate",
        "operating_cash_flow",
        "cfo_short_debt_ratio",
        "receivable_inventory_yoy_pct",
        "revenue_yoy_pct",
    ]
    financials = financials.replace([np.inf, -np.inf], np.nan)
    return financials.dropna(subset=keep)[keep].reset_index(drop=True)


def attach_peer_stats(panel: pd.DataFrame) -> pd.DataFrame:
    if panel.empty:
        return panel

    panel = panel.dropna(
        subset=["subject_rating", "issuer_median_yield_pct"]
    ).copy()
    if panel.empty:
        return panel

    grouped = panel.groupby("subject_rating", dropna=False)["issuer_median_yield_pct"]
    panel["peer_issuer_count"] = grouped.transform("count")

    def min_rank(values: pd.Series) -> pd.Series:
        ordered = sorted(values)
        return values.map(lambda value: bisect_left(ordered, value) + 1)

    # Compare Decimal values directly; pandas rank may coerce them to floats.
    rank = grouped.transform(min_rank)
    denominator = (panel["peer_issuer_count"] - 1).replace(0, np.nan)
    panel["yield_percentile_in_peer"] = (rank - 1.0) / denominator * 100.0
    return panel[panel["peer_issuer_count"] > 1].copy()


def main() -> pd.DataFrame:
    bonds = load_bond_valuations()
    if bonds.empty:
        return empty_result()

    issuer_panel = build_issuer_yield_panel(bonds)
    if issuer_panel.empty:
        return empty_result()

    companycodes = issuer_panel["companycode"].astype("int64").tolist()
    industries = fetch_sw_industries(companycodes)
    if industries.empty:
        return empty_result()

    panel = issuer_panel.merge(industries, on="companycode", how="inner")
    ratings = fetch_subject_ratings(panel["companycode"].astype("int64").tolist())
    if ratings.empty:
        return empty_result()

    panel = panel.merge(ratings, on="companycode", how="inner")
    panel = attach_peer_stats(panel)
    panel = panel[
        panel["yield_percentile_in_peer"] <= MAX_YIELD_PERCENTILE
    ].copy()
    if panel.empty:
        return empty_result()

    financials = build_financial_panel(panel["companycode"].astype("int64").tolist())
    if financials.empty:
        return empty_result()

    result = panel.merge(financials, on="companycode", how="inner")
    result["receivable_inventory_growth_gap_pct"] = (
        result["receivable_inventory_yoy_pct"] - result["revenue_yoy_pct"]
    )
    result = result[
        (result["cfo_short_debt_ratio"] <= Fraction(-1, 20))
        & (result["receivable_inventory_yoy_pct"] > 0)
        & (result["receivable_inventory_growth_gap_pct"] >= 30.0)
    ].copy()
    if result.empty:
        return empty_result()

    result = result.sort_values(
        ["yield_percentile_in_peer", "issuer"],
        ascending=[True, True],
        kind="mergesort",
    )

    result["live_credit_bond_count"] = result["live_credit_bond_count"].astype(int)
    result["fin_enddate"] = pd.to_datetime(result["fin_enddate"], errors="coerce").dt.strftime("%Y-%m-%d")
    result["cfo_short_debt_ratio"] = result["cfo_short_debt_ratio"].map(
        lambda value: float(round(value, 4))
    )
    result["issuer_median_yield_pct"] = result["issuer_median_yield_pct"].map(
        lambda value: float(round(value, 2))
    )
    for col in [
        "receivable_inventory_yoy_pct",
        "revenue_yoy_pct",
    ]:
        result[col] = result[col].map(lambda value: float(round(value, 2)))
    result["yield_percentile_in_peer"] = result["yield_percentile_in_peer"].round(2)

    output = result.rename(
        columns={
            "issuer": "主体名称",
            "subject_rating": "主体评级",
            "fin_enddate": "财报期",
            "live_credit_bond_count": "有估值存续债只数",
            "cfo_short_debt_ratio": "现金流比率",
            "receivable_inventory_yoy_pct": "应收账款与存货合计同比增速（%）",
            "revenue_yoy_pct": "营业收入同比增速（%）",
            "issuer_median_yield_pct": "估值收益率中位数（%）",
            "yield_percentile_in_peer": "组内分位（%）",
        }
    )
    return output.reset_index(drop=True)[RESULT_COLUMNS]


if __name__ == "__main__":
    main().to_csv(Path(__file__).with_name("result.csv"), index=False, encoding="utf-8-sig")
