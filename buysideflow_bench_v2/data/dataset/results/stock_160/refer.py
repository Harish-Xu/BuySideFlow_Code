"""Reference implementation for stock_160.

Build the main-board/ChiNext high-dividend observation pool as of a PIT date and
attach following 20-trading-day excess returns versus CSI 800.
"""

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


BENCHMARK_CODE = "000906"
FUTURE_WINDOW = 20
DIVIDEND_YIELD_MIN = 0.03
FISCAL_YEAR_COUNT = 3
INDUSTRY_LIMIT = 2
OUTPUT_LIMIT = 30
ACTIVE_LIST_STATUS_TYPES = "1, 3"
ST_RISK_ALERT_TYPES = "1, 5, 12, 14"
OUTPUT_COLUMNS = [
    "观察日",
    "股票代码",
    "股票简称",
    "申万一级行业",
    "股息率",
    "营业利润同比增速(%)",
    "未来20日相对中证800超额收益(%)",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build stock_160 high-dividend pool.")
    parser.add_argument("--as-of-date", default="2025-05-31")
    parser.add_argument("--output-format", choices=("json", "csv"), default="csv")
    parser.add_argument("--output-path", default="")
    return parser.parse_args()


def validate_as_of_date(as_of_date: str) -> str:
    try:
        return pd.Timestamp(as_of_date).strftime("%Y-%m-%d")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid as_of_date: {as_of_date}") from exc


def latest_complete_annual_year(as_of_date: str) -> int:
    ts = pd.Timestamp(as_of_date)
    return ts.year - 1 if (ts.month, ts.day) >= (4, 30) else ts.year - 2


def next_day(date_value: str | pd.Timestamp) -> str:
    return (pd.Timestamp(date_value) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def query_df(connector, sql: str, timeout: int = 180) -> pd.DataFrame:
    return connector.execute_sql_to_dataframe(sql, max_rows=0, timeout=timeout)


def sql_int_list(values: pd.Series | list[int]) -> str:
    unique_values = sorted(pd.Series(values).dropna().astype(int).unique().tolist())
    if not unique_values:
        raise ValueError("No integer values available for SQL IN clause")
    return ", ".join(str(value) for value in unique_values)


def normalize_int_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    for col in columns:
        if col in df.columns and not df.empty:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    return df


def normalize_code(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text.zfill(6) if text.isdigit() else text


def fetch_context(connector, as_of_date: str) -> dict[str, object]:
    df = query_df(
        connector,
        f"""
WITH trade_days AS (
    SELECT DISTINCT DATE(TRADINGDATE) AS trading_day
    FROM qt_tradingdaynew
    WHERE IFTRADINGDAY = 1
      AND SECUMARKET IN (83, 90)
),
obs AS (
    SELECT MAX(trading_day) AS obs_trade_date
    FROM trade_days
    WHERE trading_day < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
),
future AS (
    SELECT trading_day AS exit_trade_date
    FROM (
        SELECT
            td.trading_day,
            ROW_NUMBER() OVER (ORDER BY td.trading_day) AS rn
        FROM trade_days td
        JOIN obs
          ON td.trading_day > obs.obs_trade_date
    ) x
    WHERE rn = {FUTURE_WINDOW}
),
idx AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '{BENCHMARK_CODE}'
    ORDER BY INNERCODE
    LIMIT 1
)
SELECT
    DATE_FORMAT(obs.obs_trade_date, '%Y-%m-%d') AS obs_trade_date,
    DATE_FORMAT(future.exit_trade_date, '%Y-%m-%d') AS exit_trade_date,
    q_exit.CLOSEPRICE / NULLIF(q_start.CLOSEPRICE, 0) - 1 AS benchmark_return
FROM obs
JOIN future
JOIN idx
JOIN qt_indexquote q_start
  ON q_start.INNERCODE = idx.INNERCODE
 AND DATE(q_start.TRADINGDAY) = obs.obs_trade_date
JOIN qt_indexquote q_exit
  ON q_exit.INNERCODE = idx.INNERCODE
 AND DATE(q_exit.TRADINGDAY) = future.exit_trade_date;
""",
        timeout=120,
    )
    if df.empty:
        raise RuntimeError("Unable to resolve observation/exit dates or benchmark return")
    row = df.iloc[0]
    return {
        "as_of_date": as_of_date,
        "obs_trade_date": pd.Timestamp(row["obs_trade_date"]).strftime("%Y-%m-%d"),
        "exit_trade_date": pd.Timestamp(row["exit_trade_date"]).strftime("%Y-%m-%d"),
        "benchmark_return": float(row["benchmark_return"]),
        "latest_annual_year": latest_complete_annual_year(as_of_date),
    }


def fetch_stock_base(connector, context: dict[str, object]) -> pd.DataFrame:
    as_of_date = str(context["as_of_date"])
    obs_trade_date = str(context["obs_trade_date"])
    df = query_df(
        connector,
        f"""
WITH base_raw AS (
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
      AND s.LISTEDDATE < DATE_ADD(DATE('{obs_trade_date}'), INTERVAL 1 DAY)
),
latest_status AS (
    SELECT INNERCODE, CHANGETYPE
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY DATE(ls.CHANGEDATE) DESC,
                         COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
                         ls.JSID DESC,
                         ls.ID DESC
            ) AS rn
        FROM lc_liststatus ls
        JOIN base_raw b
          ON b.INNERCODE = ls.INNERCODE
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
          AND ls.CHANGEDATE <= DATE('{obs_trade_date}')
          AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY))
    ) x
    WHERE rn = 1
)
SELECT b.*
FROM base_raw b
JOIN latest_status ls
  ON ls.INNERCODE = b.INNERCODE
 AND ls.CHANGETYPE IN ({ACTIVE_LIST_STATUS_TYPES})
WHERE NOT EXISTS (
    SELECT 1
    FROM cs_riskalert ra
    WHERE ra.INNERCODE = b.INNERCODE
      AND ra.RISKALERTTYPE IN ({ST_RISK_ALERT_TYPES})
      AND DATE(ra.IMPLEMENTDATE) <= DATE('{as_of_date}')
      AND DATE(COALESCE(ra.IMPLANNOUCEDATE, ra.IMPLEMENTDATE)) <= DATE('{as_of_date}')
      AND (
          ra.REMOVEDATE IS NULL
          OR DATE(ra.REMOVEDATE) > DATE('{as_of_date}')
          OR (
              ra.REMOVEINFOPUBLDATE IS NOT NULL
              AND DATE(ra.REMOVEINFOPUBLDATE) > DATE('{as_of_date}')
          )
      )
);
""",
        timeout=180,
    )
    if df.empty:
        return pd.DataFrame(columns=["INNERCODE", "COMPANYCODE", "stock_code", "stock_name"])
    df = normalize_int_columns(df, ["INNERCODE", "COMPANYCODE"])
    df["INNERCODE"] = df["INNERCODE"].astype(int)
    df["COMPANYCODE"] = df["COMPANYCODE"].astype(int)
    df["stock_code"] = df["stock_code"].map(normalize_code)
    return df


def fetch_market_snapshot(connector, base: pd.DataFrame, context: dict[str, object]) -> pd.DataFrame:
    if base.empty:
        return pd.DataFrame(columns=["INNERCODE", "market_cap"])
    ids = sql_int_list(base["INNERCODE"])
    obs = str(context["obs_trade_date"])
    obs_next = next_day(obs)
    df = query_df(
        connector,
        f"""
SELECT
    q.INNERCODE,
    q.TURNOVERVOLUME,
    COALESCE(pd.SURGEDLIMIT, 0) AS SURGEDLIMIT,
    COALESCE(pd.DECLINELIMIT, 0) AS DECLINELIMIT,
    v.TOTALMV AS market_cap
FROM qt_dailyquote q
JOIN lc_dindicesforvaluation v
  ON v.INNERCODE = q.INNERCODE
 AND v.TRADINGDAY >= DATE('{obs}')
 AND v.TRADINGDAY < DATE('{obs_next}')
LEFT JOIN qt_performancedata pd
  ON pd.INNERCODE = q.INNERCODE
 AND pd.TRADINGDAY >= DATE('{obs}')
 AND pd.TRADINGDAY < DATE('{obs_next}')
WHERE q.INNERCODE IN ({ids})
  AND q.TRADINGDAY >= DATE('{obs}')
  AND q.TRADINGDAY < DATE('{obs_next}')
  AND q.CLOSEPRICE > 0
  AND COALESCE(q.TURNOVERVOLUME, 0) > 0
  AND v.TOTALMV IS NOT NULL
  AND v.TOTALMV > 0
  AND COALESCE(pd.SURGEDLIMIT, 0) = 0
  AND COALESCE(pd.DECLINELIMIT, 0) = 0
;
""",
        timeout=180,
    )
    if df.empty:
        return pd.DataFrame(columns=["INNERCODE", "market_cap"])
    df = normalize_int_columns(df, ["INNERCODE"])
    df["INNERCODE"] = df["INNERCODE"].astype(int)
    df["market_cap"] = pd.to_numeric(df["market_cap"], errors="coerce")
    return df[["INNERCODE", "market_cap"]]


def fetch_industry(connector, base: pd.DataFrame, context: dict[str, object]) -> pd.DataFrame:
    if base.empty:
        return pd.DataFrame(columns=["COMPANYCODE", "sw_level1_name"])
    company_ids = sql_int_list(base["COMPANYCODE"])
    as_of_next = next_day(str(context["as_of_date"]))
    df = query_df(
        connector,
        f"""
SELECT
    COMPANYCODE,
    FIRSTINDUSTRYNAME AS sw_level1_name,
    DATE_FORMAT(INFOPUBLDATE, '%Y-%m-%d') AS info_pub_date,
    DATE_FORMAT(CANCELDATE, '%Y-%m-%d') AS cancel_date,
    JSID
FROM lc_exgindustry
WHERE COMPANYCODE IN ({company_ids})
  AND STANDARD = 38
  AND INFOPUBLDATE < DATE('{as_of_next}')
  AND (CANCELDATE IS NULL OR CANCELDATE >= DATE('{as_of_next}'))
  AND FIRSTINDUSTRYNAME IS NOT NULL;
""",
        timeout=180,
    )
    if df.empty:
        return pd.DataFrame(columns=["COMPANYCODE", "sw_level1_name"])
    df = normalize_int_columns(df, ["COMPANYCODE", "JSID"])
    df["COMPANYCODE"] = df["COMPANYCODE"].astype(int)
    df["info_pub_date"] = pd.to_datetime(df["info_pub_date"])
    df["cancel_date"] = pd.to_datetime(df["cancel_date"])
    df = df.sort_values(
        ["COMPANYCODE", "info_pub_date", "cancel_date", "sw_level1_name", "JSID"],
        ascending=[True, False, False, True, False],
        na_position="last",
    )
    return df.drop_duplicates("COMPANYCODE", keep="first")[["COMPANYCODE", "sw_level1_name"]]


def fetch_latest_profit_growth(connector, base: pd.DataFrame, context: dict[str, object]) -> pd.DataFrame:
    if base.empty:
        return pd.DataFrame(columns=["COMPANYCODE", "operating_profit_yoy"])
    company_ids = sql_int_list(base["COMPANYCODE"])
    as_of_next = next_day(str(context["as_of_date"]))
    df = query_df(
        connector,
        f"""
SELECT
    COMPANYCODE,
    DATE_FORMAT(ENDDATE, '%Y-%m-%d') AS report_end_date,
    DATE_FORMAT(INFOPUBLDATE, '%Y-%m-%d') AS info_date,
    DATE_FORMAT(INSERTTIME, '%Y-%m-%d %H:%i:%s') AS insert_time,
    ID,
    JSID,
    OPERPROFITGROWRATE AS operating_profit_yoy
FROM lc_mainindexnew
WHERE COMPANYCODE IN ({company_ids})
  AND INFOPUBLDATE < DATE('{as_of_next}')
  AND OPERPROFITGROWRATE IS NOT NULL;
""",
        timeout=180,
    )
    if df.empty:
        return pd.DataFrame(columns=["COMPANYCODE", "operating_profit_yoy"])
    df = normalize_int_columns(df, ["COMPANYCODE", "ID", "JSID"])
    df["COMPANYCODE"] = df["COMPANYCODE"].astype(int)
    df["report_end_date"] = pd.to_datetime(df["report_end_date"])
    df["info_date"] = pd.to_datetime(df["info_date"])
    df["insert_time"] = pd.to_datetime(df["insert_time"])
    df["operating_profit_yoy"] = pd.to_numeric(df["operating_profit_yoy"], errors="coerce")
    df = df.sort_values(
        ["COMPANYCODE", "report_end_date", "info_date", "insert_time", "ID", "JSID"],
        ascending=[True, False, False, False, False, False],
    ).drop_duplicates("COMPANYCODE", keep="first")
    df = df[df["operating_profit_yoy"] > 0].copy()
    return df[["COMPANYCODE", "report_end_date", "operating_profit_yoy"]]


def fetch_dividends(connector, base: pd.DataFrame, context: dict[str, object]) -> pd.DataFrame:
    if base.empty:
        return pd.DataFrame(
            columns=["INNERCODE", "cash_dividend_years", "cash_dividend_12m"]
        )
    ids = sql_int_list(base["INNERCODE"])
    latest_year = int(context["latest_annual_year"])
    start_year = latest_year - FISCAL_YEAR_COUNT + 1
    as_of_date = str(context["as_of_date"])
    df = query_df(
        connector,
        f"""
SELECT
    INNERCODE,
    YEAR(ENDDATE) AS fiscal_year,
    DATE_FORMAT(ENDDATE, '%Y-%m-%d') AS end_date,
    COALESCE(PROPOSALSN, 0) AS proposal_sn,
    DATE_FORMAT(DIVIDENDIMPLEMENTDATE, '%Y-%m-%d') AS implement_date,
    TOTALCASHDIVICOMRMB,
    JSID
FROM lc_dividend
WHERE INNERCODE IN ({ids})
  AND IFDIVIDEND = 1
  AND TOTALCASHDIVICOMRMB IS NOT NULL
  AND TOTALCASHDIVICOMRMB > 0
  AND DIVIDENDIMPLEMENTDATE < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
  AND (
      YEAR(ENDDATE) BETWEEN {start_year} AND {latest_year}
      OR DIVIDENDIMPLEMENTDATE > DATE_SUB(DATE('{as_of_date}'), INTERVAL 12 MONTH)
  );
""",
        timeout=180,
    )
    if df.empty:
        return pd.DataFrame(columns=["INNERCODE", "cash_dividend_years", "cash_dividend_12m"])
    df = normalize_int_columns(df, ["INNERCODE", "JSID"])
    df["INNERCODE"] = df["INNERCODE"].astype(int)
    df["fiscal_year"] = pd.to_numeric(df["fiscal_year"], errors="coerce").astype("Int64")
    df["implement_date"] = pd.to_datetime(df["implement_date"])
    df["TOTALCASHDIVICOMRMB"] = pd.to_numeric(df["TOTALCASHDIVICOMRMB"], errors="coerce")
    df = df.sort_values(
        ["INNERCODE", "end_date", "proposal_sn", "implement_date", "JSID"],
        ascending=[True, True, True, False, False],
    ).drop_duplicates(["INNERCODE", "end_date", "proposal_sn"], keep="first")

    annual_source = df[df["fiscal_year"].between(start_year, latest_year)].copy()
    annual = (
        annual_source.groupby(["INNERCODE", "fiscal_year"], as_index=False)["TOTALCASHDIVICOMRMB"]
        .sum()
        .rename(columns={"TOTALCASHDIVICOMRMB": "annual_cash_dividend"})
    )
    required_years = (
        annual.groupby("INNERCODE")
        .agg(
            fiscal_year_count=("fiscal_year", "nunique"),
            cash_dividend_years=("annual_cash_dividend", "sum"),
        )
        .reset_index()
    )
    required_years = required_years[
        required_years["fiscal_year_count"] == FISCAL_YEAR_COUNT
    ].copy()

    window_start = pd.Timestamp(as_of_date) - pd.DateOffset(months=12)
    latest_window = df[
        (df["implement_date"] > window_start) & (df["implement_date"] <= pd.Timestamp(as_of_date))
    ]
    latest_window = (
        latest_window.groupby("INNERCODE", as_index=False)["TOTALCASHDIVICOMRMB"]
        .sum()
        .rename(columns={"TOTALCASHDIVICOMRMB": "cash_dividend_12m"})
    )
    return required_years[["INNERCODE", "cash_dividend_years"]].merge(
        latest_window, on="INNERCODE", how="inner"
    )


def fetch_future_returns(
    connector, candidates: pd.DataFrame, context: dict[str, object]
) -> pd.DataFrame:
    if candidates.empty:
        return pd.DataFrame(columns=["INNERCODE", "future_excess_return"])
    ids = sql_int_list(candidates["INNERCODE"])
    obs = str(context["obs_trade_date"])
    exit_date = str(context["exit_trade_date"])
    df = query_df(
        connector,
        f"""
SELECT
    INNERCODE,
    DATE_FORMAT(TRADINGDAY, '%Y-%m-%d') AS trading_day,
    BACKWARDPRICE
FROM qt_performancedata
WHERE INNERCODE IN ({ids})
  AND DATE(TRADINGDAY) IN (DATE('{obs}'), DATE('{exit_date}'))
  AND BACKWARDPRICE IS NOT NULL
  AND BACKWARDPRICE > 0;
""",
        timeout=120,
    )
    if df.empty:
        return pd.DataFrame(columns=["INNERCODE", "future_excess_return"])
    df = normalize_int_columns(df, ["INNERCODE"])
    df["INNERCODE"] = df["INNERCODE"].astype(int)
    df["BACKWARDPRICE"] = pd.to_numeric(df["BACKWARDPRICE"], errors="coerce")
    prices = df.pivot_table(index="INNERCODE", columns="trading_day", values="BACKWARDPRICE", aggfunc="last")
    if obs not in prices.columns or exit_date not in prices.columns:
        return pd.DataFrame(columns=["INNERCODE", "future_excess_return"])
    returns = prices[[obs, exit_date]].dropna().copy()
    returns = returns[(returns[obs] > 0) & (returns[exit_date] > 0)]
    returns["future_excess_return"] = (
        returns[exit_date] / returns[obs] - 1 - float(context["benchmark_return"])
    )
    return returns[["future_excess_return"]].reset_index()


def build_result(connector, as_of_date: str) -> pd.DataFrame:
    context = fetch_context(connector, as_of_date)
    base = fetch_stock_base(connector, context)
    if base.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    market = fetch_market_snapshot(connector, base, context)
    industry = fetch_industry(connector, base, context)
    profit_growth = fetch_latest_profit_growth(connector, base, context)
    dividends = fetch_dividends(connector, base, context)

    candidates = (
        base.merge(market, on="INNERCODE", how="inner")
        .merge(industry, on="COMPANYCODE", how="inner")
        .merge(profit_growth, on="COMPANYCODE", how="inner")
        .merge(dividends, on="INNERCODE", how="inner")
    )
    if candidates.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    candidates["dividend_yield"] = candidates["cash_dividend_12m"] / candidates["market_cap"]
    candidates = candidates[candidates["dividend_yield"] > DIVIDEND_YIELD_MIN].copy()
    if candidates.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    candidates = candidates.sort_values(
        ["sw_level1_name", "dividend_yield", "stock_code"],
        ascending=[True, False, True],
    )
    candidates["industry_rank"] = candidates.groupby("sw_level1_name").cumcount() + 1
    capped = candidates[candidates["industry_rank"] <= INDUSTRY_LIMIT].copy()
    capped = capped.sort_values(["dividend_yield", "stock_code"], ascending=[False, True])
    top = capped.head(OUTPUT_LIMIT).copy()

    future_returns = fetch_future_returns(connector, top, context)
    result = top.merge(future_returns, on="INNERCODE", how="inner")
    if result.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    result = result.sort_values(["dividend_yield", "stock_code"], ascending=[False, True])

    output = pd.DataFrame(
        {
            "观察日": str(context["as_of_date"]),
            "股票代码": result["stock_code"].map(normalize_code),
            "股票简称": result["stock_name"],
            "申万一级行业": result["sw_level1_name"],
            "股息率": (
                pd.to_numeric(result["dividend_yield"], errors="coerce") * 100
            ).round(2),
            "营业利润同比增速(%)": pd.to_numeric(
                result["operating_profit_yoy"], errors="coerce"
            ).round(2),
            "未来20日相对中证800超额收益(%)": pd.to_numeric(
                result["future_excess_return"], errors="coerce"
            ).mul(100).round(2),
        }
    )
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
    result = build_result(connector, as_of_date)
    write_output(result, args.output_format, args.output_path)


if __name__ == "__main__":
    main()
