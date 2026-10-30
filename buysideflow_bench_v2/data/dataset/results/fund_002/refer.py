#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

try:
    import matplotlib

    matplotlib.use("Agg")
except Exception:
    pass

import matplotlib.pyplot as plt

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


AS_OF_DATE = "2025-09-12"
REPORT_START_DATE = "2022-01-01"
SCRIPT_DIR = Path(__file__).resolve().parent
SW_CHEMICAL = "基础化工"


def _date_sql_list(dates: list[str]) -> str:
    return ", ".join(f"DATE('{date}')" for date in dates)


def candidate_report_dates() -> list[str]:
    start = pd.Timestamp(REPORT_START_DATE)
    end = pd.Timestamp(AS_OF_DATE)
    dates: list[str] = []
    for year in range(start.year, end.year + 1):
        for month_day in ("06-30", "12-31"):
            date = pd.Timestamp(f"{year}-{month_day}")
            if start <= date <= end:
                dates.append(date.strftime("%Y-%m-%d"))
    return dates


def fetch_published_report_dates() -> list[str]:
    candidates = candidate_report_dates()
    sql = f"""
    SELECT DATE(REPORTDATE) AS report_date
    FROM mf_stockportfoliodetail
    WHERE REPORTDATE IN ({_date_sql_list(candidates)})
      AND INFOPUBLDATE < DATE_ADD('{AS_OF_DATE}', INTERVAL 1 DAY)
    GROUP BY DATE(REPORTDATE)
    ORDER BY report_date
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    return [pd.Timestamp(x).strftime("%Y-%m-%d") for x in df["report_date"].dropna()]


def fetch_fund_holding_by_company(report_dates: list[str]) -> pd.DataFrame:
    sql = f"""
    WITH holding_funds AS (
        SELECT DISTINCT sp.REPORTDATE AS report_date, sp.INNERCODE
        FROM mf_stockportfoliodetail sp
        WHERE sp.REPORTDATE IN ({_date_sql_list(report_dates)})
          AND sp.INFOPUBLDATE < DATE_ADD('{AS_OF_DATE}', INTERVAL 1 DAY)
          AND sp.INSERTTIME < DATE_ADD('{AS_OF_DATE}', INTERVAL 1 DAY)
    ),
    fund_class_rank AS (
        SELECT
            hf.report_date,
            hf.INNERCODE,
            ft.SECASSETCATCODE,
            ROW_NUMBER() OVER (
                PARTITION BY hf.report_date, hf.INNERCODE
                ORDER BY ft.EFFECTIVEDATE DESC, ft.INSERTTIME DESC, ft.ID DESC
            ) AS rn
        FROM holding_funds hf
        JOIN mf_jyfundtype ft
         ON ft.INNERCODE = hf.INNERCODE
         AND ft.STANDARD = 75
         AND ft.EFFECTIVEDATE <= hf.report_date
         AND (ft.CANCELDATE IS NULL OR ft.CANCELDATE > hf.report_date)
         AND ft.INSERTTIME < DATE_ADD('{AS_OF_DATE}', INTERVAL 1 DAY)
    ),
    sample_funds AS (
        SELECT fcr.report_date, fcr.INNERCODE
        FROM fund_class_rank fcr
        JOIN mf_fundarchives fa
          ON fa.INNERCODE = fcr.INNERCODE
        WHERE fcr.rn = 1
          AND fcr.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
          AND fa.INVESTMENTTYPE = 16
          AND COALESCE(fa.IFFOF, 2) <> 1
          AND COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
    )
    SELECT
        DATE(sp.REPORTDATE) AS report_date,
        s.COMPANYCODE,
        SUM(sp.MARKETVALUE) AS fund_mv
    FROM sample_funds sf
    JOIN mf_stockportfoliodetail sp
      ON sp.INNERCODE = sf.INNERCODE
     AND sp.REPORTDATE = sf.report_date
     AND sp.INFOPUBLDATE < DATE_ADD('{AS_OF_DATE}', INTERVAL 1 DAY)
     AND sp.INSERTTIME < DATE_ADD('{AS_OF_DATE}', INTERVAL 1 DAY)
    LEFT JOIN secumain s
      ON s.INNERCODE = sp.STOCKINNERCODE
     AND s.SECUCATEGORY = 1
     AND s.SECUMARKET IN (83, 90)
    GROUP BY DATE(sp.REPORTDATE), s.COMPANYCODE
    ORDER BY report_date, s.COMPANYCODE
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=900)
    df["report_date"] = pd.to_datetime(df["report_date"]).dt.strftime("%Y-%m-%d")
    return df


def fetch_market_trade_dates(report_dates: list[str]) -> dict[str, str]:
    report_cte = "\nUNION ALL\n".join(
        f"SELECT DATE('{report_date}') AS report_date" for report_date in report_dates
    )
    sql = f"""
    WITH report_dates AS (
        {report_cte}
    )
    SELECT
        DATE(rd.report_date) AS report_date,
        DATE(MAX(td.TRADINGDATE)) AS trade_date
    FROM report_dates rd
    JOIN qt_tradingdaynew td
      ON td.SECUMARKET = 83
     AND td.IFTRADINGDAY = 1
     AND td.TRADINGDATE <= rd.report_date
    GROUP BY DATE(rd.report_date)
    ORDER BY report_date
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=300)
    return {
        pd.Timestamp(row["report_date"]).strftime("%Y-%m-%d"): pd.Timestamp(row["trade_date"]).strftime("%Y-%m-%d")
        for _, row in df.iterrows()
    }


def fetch_market_float_by_company(report_to_trade: dict[str, str]) -> pd.DataFrame:
    trade_dates = sorted(set(report_to_trade.values()))
    sql = f"""
    SELECT
        DATE(v.TRADINGDAY) AS trade_date,
        s.COMPANYCODE,
        SUM(v.NEGOTIABLEMV) AS float_mv
    FROM lc_dindicesforvaluation v
    JOIN secumain s
      ON s.INNERCODE = v.INNERCODE
     AND s.SECUCATEGORY = 1
     AND s.SECUMARKET IN (83, 90)
    WHERE v.TRADINGDAY IN ({_date_sql_list(trade_dates)})
      AND v.NEGOTIABLEMV IS NOT NULL
    GROUP BY DATE(v.TRADINGDAY), s.COMPANYCODE
    ORDER BY trade_date, s.COMPANYCODE
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=900)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
    trade_to_report = {trade: report for report, trade in report_to_trade.items()}
    df["report_date"] = df["trade_date"].map(trade_to_report)
    return df


def fetch_chemical_history(report_dates: list[str]) -> pd.DataFrame:
    min_date = min(report_dates)
    max_date = max(report_dates)
    sql = f"""
    SELECT
        COMPANYCODE,
        FIRSTINDUSTRYNAME,
        DATE(INFOPUBLDATE) AS effective_date,
        DATE(INFOPUBLDATE) AS info_date,
        DATE(CANCELDATE) AS cancel_date
    FROM lc_exgindustry
    WHERE STANDARD = 38
      AND FIRSTINDUSTRYNAME = '{SW_CHEMICAL}'
      AND INFOPUBLDATE <= '{max_date}'
      AND (CANCELDATE IS NULL OR CANCELDATE > '{min_date}')
    UNION ALL
    SELECT
        COMPANYCODE,
        FIRSTINDUSTRYNAME,
        DATE(EFFECTIVEDATE) AS effective_date,
        DATE(INFOPUBLDATE) AS info_date,
        DATE(CANCELDATE) AS cancel_date
    FROM lc_stibexgindustry
    WHERE STANDARD = 38
      AND FIRSTINDUSTRYNAME = '{SW_CHEMICAL}'
      AND EFFECTIVEDATE <= '{max_date}'
      AND INFOPUBLDATE <= '{max_date}'
      AND (CANCELDATE IS NULL OR CANCELDATE > '{min_date}')
    """
    df = query_to_dataframe(sql, max_rows=0, timeout=300)
    for col in ("effective_date", "info_date", "cancel_date"):
        df[col] = pd.to_datetime(df[col])
    return df


def chemical_company_set(history: pd.DataFrame, report_date: str) -> set[float]:
    date = pd.Timestamp(report_date)
    valid = history[
        (history["effective_date"] <= date)
        & (history["info_date"] <= date)
        & (history["cancel_date"].isna() | (history["cancel_date"] > date))
    ]
    return set(valid["COMPANYCODE"].dropna().astype(float))


def ratio_for_date(df: pd.DataFrame, value_col: str, chemical_companies: set[float]) -> float:
    total = df[value_col].sum()
    if total == 0:
        return float("nan")
    chemical = df.loc[df["COMPANYCODE"].astype(float).isin(chemical_companies), value_col].sum()
    return chemical / total * 100


def build_ratio_series() -> pd.DataFrame:
    report_dates = fetch_published_report_dates()
    if not report_dates:
        return pd.DataFrame()

    fund_holding = fetch_fund_holding_by_company(report_dates)
    report_to_trade = fetch_market_trade_dates(report_dates)
    market_float = fetch_market_float_by_company(report_to_trade)
    chemical_history = fetch_chemical_history(report_dates)

    rows: list[dict[str, object]] = []
    for report_date in report_dates:
        chemicals = chemical_company_set(chemical_history, report_date)
        fund_slice = fund_holding[fund_holding["report_date"] == report_date]
        market_slice = market_float[market_float["report_date"] == report_date]
        fund_ratio = ratio_for_date(fund_slice, "fund_mv", chemicals)
        market_ratio = ratio_for_date(market_slice, "float_mv", chemicals)
        rows.append(
            {
                "报告期": report_date,
                "基金基础化工持仓占比(%)": round(fund_ratio, 2),
                "基础化工流通市值占比(%)": round(market_ratio, 2),
                "超配比例(%)": round(fund_ratio - market_ratio, 2),
            }
        )
    return pd.DataFrame(rows)


def plot_ratio_series(ratio_df: pd.DataFrame, output_path: Path) -> None:
    plot_df = ratio_df.copy()
    plot_df["报告期"] = pd.to_datetime(plot_df["报告期"])

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(plot_df["报告期"], plot_df["基金基础化工持仓占比(%)"], marker="o", label="Fund holding")
    ax.plot(plot_df["报告期"], plot_df["基础化工流通市值占比(%)"], marker="s", label="Market float cap")
    ax.plot(plot_df["报告期"], plot_df["超配比例(%)"], marker="^", linestyle="--", label="Overweight")
    ax.set_title("Basic Chemical Allocation vs Market Float-Cap Share")
    ax.set_ylabel("Percent (%)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    plt.xticks(rotation=45)
    plt.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def main() -> pd.DataFrame:
    ratio_df = build_ratio_series()
    if ratio_df.empty:
        raise ValueError("结果为空，请检查报告期、披露日和样本基金口径。")
    ratio_df.to_csv(SCRIPT_DIR / "result.csv", index=False, encoding="utf-8-sig", float_format="%.2f")
    plot_ratio_series(ratio_df, SCRIPT_DIR / "picture.png")
    print(ratio_df.to_string(index=False))
    return ratio_df


if __name__ == "__main__":
    main()
