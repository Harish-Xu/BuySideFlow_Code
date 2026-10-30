"""Reference implementation for stock_163: three-factor union small-cap screen."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import get_db_connector


FINAL_LIMIT = 10
TOP_DECILE = 0.10
RISK_ALERT_TYPES = "1, 5, 12, 14"
QUERY_CHUNK_SIZE = 700
OUTPUT_COLUMNS = [
    "股票代码",
    "股票简称",
    "股息率(%)",
    "PE",
    "近五年营业收入增幅(%)",
    "总市值(亿元)",
    "高股息标记",
    "低PE标记",
    "高营业收入增长标记",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="stock_163 three-factor union small-cap reference implementation."
    )
    parser.add_argument("--as-of-date", default="2025-12-31")
    parser.add_argument("--output-format", choices=("json", "csv"), default="csv")
    parser.add_argument("--output-path", default="")
    return parser.parse_args()


def validate_as_of_date(as_of_date: str) -> str:
    try:
        return pd.Timestamp(as_of_date).strftime("%Y-%m-%d")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid as_of_date: {as_of_date}") from exc


def next_day(date_value: str | pd.Timestamp) -> str:
    return (pd.Timestamp(date_value) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def query_df(connector, sql: str, timeout: int = 90) -> pd.DataFrame:
    return connector.execute_sql_to_dataframe(sql, max_rows=0, timeout=timeout)


def query_integer_chunks(
    connector,
    sql_template: str,
    values: pd.Series,
    placeholder: str,
    timeout: int = 90,
) -> pd.DataFrame:
    unique_values = sorted(pd.to_numeric(values, errors="coerce").dropna().astype(int).unique())
    frames: list[pd.DataFrame] = []
    for start in range(0, len(unique_values), QUERY_CHUNK_SIZE):
        chunk = unique_values[start : start + QUERY_CHUNK_SIZE]
        sql_values = ", ".join(str(value) for value in chunk)
        frames.append(query_df(connector, sql_template.replace(placeholder, sql_values), timeout))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def id_set(df: pd.DataFrame, column: str = "INNERCODE") -> set[int]:
    if df.empty or column not in df.columns:
        return set()
    return set(pd.to_numeric(df[column], errors="coerce").dropna().astype(int))


def fetch_trade_date(connector, as_of_date: str) -> str:
    df = query_df(
        connector,
        f"""
SELECT MAX(DATE(TRADINGDATE)) AS trade_date
FROM qt_tradingdaynew
WHERE IFTRADINGDAY = 1
  AND SECUMARKET IN (83, 90)
  AND TRADINGDATE < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY);
""",
        timeout=60,
    )
    if df.empty or pd.isna(df.loc[0, "trade_date"]):
        raise RuntimeError(f"No A-share trading day found on or before {as_of_date}")
    return pd.Timestamp(df.loc[0, "trade_date"]).strftime("%Y-%m-%d")


def fetch_stock_base(connector, trade_date: str) -> pd.DataFrame:
    trade_next = next_day(trade_date)
    base = query_df(
        connector,
        f"""
SELECT
    s.INNERCODE,
    s.COMPANYCODE,
    s.SECUCODE AS stock_code,
    COALESCE(s.CHINAMEABBR, s.SECUABBR, s.CHINAME, s.SECUCODE) AS stock_name
FROM secumain s
WHERE s.SECUCATEGORY = 1
  AND s.SECUMARKET IN (83, 90)
  AND s.LISTEDSECTOR IN (1, 2, 6)
  AND s.COMPANYCODE IS NOT NULL
  AND s.LISTEDDATE <= DATE_SUB(DATE('{trade_date}'), INTERVAL 1 YEAR);
""",
        timeout=60,
    )
    if base.empty:
        return pd.DataFrame(columns=["INNERCODE", "COMPANYCODE", "stock_code", "stock_name"])

    risk = query_df(
        connector,
        f"""
SELECT DISTINCT INNERCODE
FROM cs_riskalert
WHERE RISKALERTTYPE IN ({RISK_ALERT_TYPES})
  AND IMPLEMENTDATE < DATE('{trade_next}')
  AND COALESCE(IMPLANNOUCEDATE, IMPLEMENTDATE) < DATE('{trade_next}')
  AND (
      REMOVEDATE IS NULL
      OR REMOVEDATE >= DATE('{trade_next}')
      OR (
          REMOVEINFOPUBLDATE IS NOT NULL
          AND REMOVEINFOPUBLDATE >= DATE('{trade_next}')
      )
  );
""",
        timeout=60,
    )

    base["INNERCODE"] = pd.to_numeric(base["INNERCODE"]).astype(int)
    base["COMPANYCODE"] = pd.to_numeric(base["COMPANYCODE"]).astype(int)
    base["stock_code"] = base["stock_code"].astype(str).str.zfill(6)
    blocked = id_set(risk)
    if blocked:
        base = base[~base["INNERCODE"].isin(blocked)]
    return base.reset_index(drop=True)


def fetch_market_snapshot(
    connector, trade_date: str, universe: pd.Series
) -> pd.DataFrame:
    trade_next = next_day(trade_date)
    valuation = query_integer_chunks(
        connector,
        f"""
SELECT
    INNERCODE,
    TOTALMV AS market_cap,
    PE AS pe_ratio
FROM lc_dindicesforvaluation
WHERE INNERCODE IN (__INNERCODES__)
  AND TRADINGDAY >= DATE('{trade_date}')
  AND TRADINGDAY < DATE('{trade_next}')
  AND TOTALMV IS NOT NULL
  AND TOTALMV > 0;
""",
        universe,
        "__INNERCODES__",
    )
    quote = query_integer_chunks(
        connector,
        f"""
SELECT INNERCODE, TURNOVERVOLUME
FROM qt_dailyquote
WHERE INNERCODE IN (__INNERCODES__)
  AND TRADINGDAY >= DATE('{trade_date}')
  AND TRADINGDAY < DATE('{trade_next}');
""",
        universe,
        "__INNERCODES__",
    )
    performance = query_integer_chunks(
        connector,
        f"""
SELECT INNERCODE, SURGEDLIMIT, DECLINELIMIT
FROM qt_performancedata
WHERE INNERCODE IN (__INNERCODES__)
  AND TRADINGDAY >= DATE('{trade_date}')
  AND TRADINGDAY < DATE('{trade_next}');
""",
        universe,
        "__INNERCODES__",
    )
    if valuation.empty or quote.empty or performance.empty:
        return pd.DataFrame(
            columns=[
                "INNERCODE",
                "market_cap",
                "pe_ratio",
                "SURGEDLIMIT",
                "DECLINELIMIT",
            ]
        )

    for df in (valuation, quote, performance):
        if "INNERCODE" in df.columns and not df.empty:
            df["INNERCODE"] = pd.to_numeric(df["INNERCODE"], errors="coerce").astype(int)
    for col in ["market_cap", "pe_ratio"]:
        valuation[col] = pd.to_numeric(valuation[col], errors="coerce")
    quote["TURNOVERVOLUME"] = pd.to_numeric(quote["TURNOVERVOLUME"], errors="coerce").fillna(0)
    performance["SURGEDLIMIT"] = pd.to_numeric(
        performance.get("SURGEDLIMIT"), errors="coerce"
    )
    performance["DECLINELIMIT"] = pd.to_numeric(
        performance.get("DECLINELIMIT"), errors="coerce"
    )

    market = valuation.merge(quote, on="INNERCODE", how="inner")
    market = market.merge(performance, on="INNERCODE", how="inner")
    market = market[
        (market["TURNOVERVOLUME"] > 0)
        & market["SURGEDLIMIT"].eq(0)
        & market["DECLINELIMIT"].eq(0)
    ]
    return market.reset_index(drop=True)


def fetch_financial_snapshots(
    connector,
    trade_date: str,
    current_report: str,
    five_year_report: str,
    company_codes: pd.Series,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    trade_next = next_day(trade_date)
    financial = query_integer_chunks(
        connector,
        f"""
SELECT
    COMPANYCODE,
    DATE_FORMAT(ENDDATE, '%Y-%m-%d') AS end_date,
    DATE_FORMAT(INFOPUBLDATE, '%Y-%m-%d') AS info_date,
    DATE_FORMAT(INSERTTIME, '%Y-%m-%d %H:%i:%s') AS insert_time,
    MARK,
    ID,
    JSID,
    EPS,
    OPERATINGREENUE
FROM lc_maindatanew
WHERE COMPANYCODE IN (__COMPANYCODES__)
  AND ENDDATE IN (DATE('{current_report}'), DATE('{five_year_report}'))
  AND INFOPUBLDATE < DATE('{trade_next}')
  AND MARK IN (1, 8, 7, 6, 2);
""",
        company_codes,
        "__COMPANYCODES__",
        timeout=120,
    )
    if financial.empty:
        empty_current = pd.DataFrame(
            columns=["COMPANYCODE", "report_end_date", "eps", "operating_revenue"]
        )
        empty_prior = pd.DataFrame(columns=["COMPANYCODE", "operating_revenue_5y"])
        return empty_current, empty_prior

    financial["COMPANYCODE"] = pd.to_numeric(financial["COMPANYCODE"]).astype(int)
    financial["info_date"] = pd.to_datetime(financial["info_date"])
    financial["insert_time"] = pd.to_datetime(financial["insert_time"])
    financial["mark_order"] = (
        pd.to_numeric(financial["MARK"], errors="coerce")
        .map({1: 1, 8: 2, 7: 3, 6: 4, 2: 5})
        .fillna(99)
        .astype(int)
    )
    financial["ID"] = pd.to_numeric(financial["ID"], errors="coerce")
    financial["JSID"] = pd.to_numeric(financial["JSID"], errors="coerce")
    financial["EPS"] = pd.to_numeric(financial["EPS"], errors="coerce")
    financial["OPERATINGREENUE"] = pd.to_numeric(financial["OPERATINGREENUE"], errors="coerce")
    financial = financial.sort_values(
        ["COMPANYCODE", "end_date", "mark_order", "info_date", "insert_time", "ID", "JSID"],
        ascending=[True, True, True, False, False, False, False],
    )
    financial = financial.drop_duplicates(["COMPANYCODE", "end_date"], keep="first")

    current = financial[financial["end_date"] == current_report][
        ["COMPANYCODE", "end_date", "EPS", "OPERATINGREENUE"]
    ].rename(
        columns={
            "end_date": "report_end_date",
            "EPS": "eps",
            "OPERATINGREENUE": "operating_revenue",
        }
    )
    prior = financial[financial["end_date"] == five_year_report][
        ["COMPANYCODE", "OPERATINGREENUE"]
    ].rename(columns={"OPERATINGREENUE": "operating_revenue_5y"})
    return current, prior


def fetch_dividends(connector, trade_date: str, universe: pd.Series) -> pd.DataFrame:
    trade_next = next_day(trade_date)
    dividend = query_integer_chunks(
        connector,
        f"""
SELECT
    INNERCODE,
    DATE_FORMAT(ENDDATE, '%Y-%m-%d') AS end_date,
    COALESCE(PROPOSALSN, 0) AS proposal_sn,
    DATE_FORMAT(EXDIVIDATE, '%Y-%m-%d') AS ex_date,
    TOTALCASHDIVICOMRMB,
    JSID
FROM lc_dividend
WHERE INNERCODE IN (__INNERCODES__)
  AND IFDIVIDEND = 1
  AND TOTALCASHDIVICOMRMB IS NOT NULL
  AND EXDIVIDATE > DATE_SUB(DATE('{trade_date}'), INTERVAL 1 YEAR)
  AND EXDIVIDATE < DATE('{trade_next}')
  AND DIVIDENDIMPLEMENTDATE < DATE('{trade_next}');
""",
        universe,
        "__INNERCODES__",
        timeout=120,
    )
    if dividend.empty:
        return pd.DataFrame(columns=["INNERCODE", "cash_dividend"])

    dividend["INNERCODE"] = pd.to_numeric(dividend["INNERCODE"], errors="coerce").astype(int)
    dividend["TOTALCASHDIVICOMRMB"] = pd.to_numeric(dividend["TOTALCASHDIVICOMRMB"])
    dividend["ex_date"] = pd.to_datetime(dividend["ex_date"])
    dividend["JSID"] = pd.to_numeric(dividend["JSID"], errors="coerce")
    dividend = dividend.sort_values(
        ["INNERCODE", "end_date", "proposal_sn", "ex_date", "JSID"],
        ascending=[True, True, True, False, False],
    )
    dividend = dividend.drop_duplicates(["INNERCODE", "end_date", "proposal_sn"], keep="first")
    return (
        dividend.groupby("INNERCODE", as_index=False)["TOTALCASHDIVICOMRMB"]
        .sum()
        .rename(columns={"TOTALCASHDIVICOMRMB": "cash_dividend"})
    )


def select_top_decile(
    frame: pd.DataFrame,
    factor: str,
    *,
    ascending: bool,
) -> pd.DataFrame:
    ranked = frame[frame[factor].notna()].copy()
    if ranked.empty:
        return ranked
    if len(ranked) == 1:
        ranked["factor_percent_rank"] = 0.0
    else:
        ranks = ranked[factor].rank(method="min", ascending=ascending)
        ranked["factor_percent_rank"] = (ranks - 1) / (len(ranked) - 1)
    return ranked[ranked["factor_percent_rank"] <= TOP_DECILE]


def compute_result(connector, as_of_date: str) -> pd.DataFrame:
    trade_date = fetch_trade_date(connector, as_of_date)
    current_report_str = "2025-09-30"
    five_year_report_str = "2020-09-30"

    base = fetch_stock_base(connector, trade_date)
    if base.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    market = fetch_market_snapshot(connector, trade_date, base["INNERCODE"])
    current_financial, five_year_financial = fetch_financial_snapshots(
        connector,
        trade_date,
        current_report_str,
        five_year_report_str,
        base["COMPANYCODE"],
    )
    dividends = fetch_dividends(connector, trade_date, base["INNERCODE"])

    factor_base = (
        base.merge(market, on="INNERCODE", how="inner")
        .merge(current_financial, on="COMPANYCODE", how="inner")
        .merge(five_year_financial, on="COMPANYCODE", how="left")
        .merge(dividends, on="INNERCODE", how="left")
    )
    if factor_base.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    factor_base = factor_base[factor_base["eps"] > 0].copy()
    if factor_base.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    factor_base["cash_dividend"] = factor_base["cash_dividend"].fillna(0)
    factor_base["dividend_yield"] = factor_base["cash_dividend"] / factor_base["market_cap"]
    factor_base["sales_growth"] = pd.NA
    valid_sales_base = factor_base["operating_revenue_5y"] > 0
    factor_base.loc[valid_sales_base, "sales_growth"] = (
        factor_base.loc[valid_sales_base, "operating_revenue"]
        / factor_base.loc[valid_sales_base, "operating_revenue_5y"]
        - 1
    )
    factor_base["sales_growth"] = pd.to_numeric(
        factor_base["sales_growth"], errors="coerce"
    )

    high_dividend = select_top_decile(factor_base, "dividend_yield", ascending=False)
    low_pe = select_top_decile(
        factor_base[factor_base["pe_ratio"] > 0],
        "pe_ratio",
        ascending=True,
    )
    high_sales = select_top_decile(factor_base, "sales_growth", ascending=False)

    selected = (
        set(high_dividend["INNERCODE"].astype(int))
        | set(low_pe["INNERCODE"].astype(int))
        | set(high_sales["INNERCODE"].astype(int))
    )
    if not selected:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    result = factor_base[factor_base["INNERCODE"].isin(selected)].copy()
    result["in_high_dividend_pool"] = result["INNERCODE"].isin(
        set(high_dividend["INNERCODE"].astype(int))
    ).astype(int)
    result["in_low_pe_pool"] = result["INNERCODE"].isin(
        set(low_pe["INNERCODE"].astype(int))
    ).astype(int)
    result["in_high_sales_growth_pool"] = result["INNERCODE"].isin(
        set(high_sales["INNERCODE"].astype(int))
    ).astype(int)
    result = result.sort_values(["market_cap", "stock_code"]).head(FINAL_LIMIT).copy()
    output = pd.DataFrame(
        {
            "股票代码": result["stock_code"],
            "股票简称": result["stock_name"],
            "股息率(%)": result["dividend_yield"] * 100,
            "PE": result["pe_ratio"],
            "近五年营业收入增幅(%)": result["sales_growth"] * 100,
            "总市值(亿元)": result["market_cap"] / 100000000,
            "高股息标记": result["in_high_dividend_pool"],
            "低PE标记": result["in_low_pe_pool"],
            "高营业收入增长标记": result["in_high_sales_growth_pool"],
        }
    )
    for column in [
        "股息率(%)",
        "PE",
        "近五年营业收入增幅(%)",
        "总市值(亿元)",
    ]:
        output[column] = pd.to_numeric(output[column], errors="coerce").round(2)
    return output[OUTPUT_COLUMNS].reset_index(drop=True)


def write_output(result: pd.DataFrame, output_format: str, output_path: str) -> None:
    if output_format == "json":
        content = json.dumps(result.to_dict(orient="records"), ensure_ascii=False, indent=2)
    else:
        buf = io.StringIO()
        result.to_csv(buf, index=False, float_format="%.2f")
        content = buf.getvalue()

    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"Saved output to {path}")
        return
    print(content)


def main() -> None:
    args = parse_args()
    as_of_date = validate_as_of_date(args.as_of_date)
    if not args.output_path:
        args.output_path = str(Path(__file__).with_name("result.csv"))
    if args.output_format == "json" and args.output_path.endswith(".csv"):
        args.output_format = "csv"

    connector = get_db_connector()
    try:
        result = compute_result(connector, as_of_date)
        write_output(result, args.output_format, args.output_path)
    finally:
        connector.close_all()


if __name__ == "__main__":
    main()
