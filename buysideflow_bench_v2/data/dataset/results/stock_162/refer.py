"""Reference implementation for stock_162.

Compare small-cap GARP and small-cap value candidate pools using PIT data and
following 60-trading-day excess returns versus CSI 1000.
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


BENCHMARK_CODE = "000852"
FUTURE_WINDOW = 60
SMALL_CAP_FRACTION = 0.30
PORTFOLIO_LIMIT = 20
MAX_PE = 30
MAX_PB = 1.5
MIN_REVENUE_YOY_PERCENT = 15
MAX_PEG = 1
ACTIVE_LIST_STATUS_TYPES = "1, 3, 6"  # 6 = 退市整理期: still listed and trading at the observation date
ST_RISK_ALERT_TYPES = "1, 5, 12, 14"
OUTPUT_COLUMNS = [
    "组合类型",
    "有效样本数",
    "总市值中位数(亿元)",
    "等权平均超额收益(%)",
    "超额收益胜率(%)",
]


def canonical_as_of_date() -> str:
    from sweagent.text2sql import db_connector
    root = Path(db_connector.__file__).resolve().parents[2]
    with (root / "data/dataset/question/dataset.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            task = json.loads(line)
            if task["instance_id"] == "stock_162":
                return validate_as_of_date(task["inputs"]["as_of_date"])
    raise ValueError("stock_162 missing from canonical dataset")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build stock_162 factor-pool performance summary.")
    parser.add_argument("--as-of-date", default=None)
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
        return pd.DataFrame(columns=["INNERCODE", "market_cap", "pe", "pb"])
    ids = sql_int_list(base["INNERCODE"])
    obs = str(context["obs_trade_date"])
    obs_next = next_day(obs)
    df = query_df(
        connector,
        f"""
SELECT
    q.INNERCODE,
    v.TOTALMV AS market_cap,
    v.PE AS pe,
    v.PB AS pb
FROM qt_dailyquote q
JOIN lc_dindicesforvaluation v
  ON v.INNERCODE = q.INNERCODE
 AND v.TRADINGDAY >= DATE('{obs}')
 AND v.TRADINGDAY < DATE('{obs_next}')
WHERE q.INNERCODE IN ({ids})
  AND q.TRADINGDAY >= DATE('{obs}')
  AND q.TRADINGDAY < DATE('{obs_next}')
  AND q.CLOSEPRICE > 0
  AND COALESCE(q.TURNOVERVOLUME, 0) > 0
  AND v.TOTALMV IS NOT NULL
  AND v.TOTALMV > 0
  AND NOT EXISTS (
      SELECT 1 FROM qt_pricelimit pl
      WHERE pl.INNERCODE = q.INNERCODE AND pl.TRADINGDAY = q.TRADINGDAY
        AND pl.INSERTTIME < DATE('{obs_next}')
        AND (q.CLOSEPRICE = pl.PRICECEILING OR q.CLOSEPRICE = pl.PRICEFLOOR)
  )
  AND NOT EXISTS (
      SELECT 1 FROM lc_suspendresumption sr
      WHERE sr.INNERCODE = q.INNERCODE AND sr.SUSPENDDATE < DATE('{obs_next}')
        AND COALESCE(sr.INFOPUBLDATE, sr.INSERTTIME) < DATE('{obs_next}')
        AND (sr.RESUMPTIONDATE IS NULL OR sr.RESUMPTIONDATE >= DATE('{obs_next}'))
  )
;
""",
        timeout=180,
    )
    if df.empty:
        return pd.DataFrame(columns=["INNERCODE", "market_cap", "pe", "pb"])
    df = normalize_int_columns(df, ["INNERCODE"])
    df["INNERCODE"] = df["INNERCODE"].astype(int)
    for col in ["market_cap", "pe", "pb"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[["INNERCODE", "market_cap", "pe", "pb"]]


def fetch_latest_factors(connector, base: pd.DataFrame, context: dict[str, object]) -> pd.DataFrame:
    if base.empty:
        return pd.DataFrame(columns=["COMPANYCODE", "revenue_yoy", "adjusted_profit_yoy", "roe"])
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
    OPERATINGREVENUEGROWRATE AS revenue_yoy,
    NPPARENTCOMPANYCUTYOY AS adjusted_profit_yoy,
    ROE AS roe
FROM lc_mainindexnew
WHERE COMPANYCODE IN ({company_ids})
  AND INFOPUBLDATE < DATE('{as_of_next}')
  AND ENDDATE < DATE('{as_of_next}');
""",
        timeout=180,
    )
    if df.empty:
        return pd.DataFrame(columns=["COMPANYCODE", "revenue_yoy", "adjusted_profit_yoy", "roe"])
    df = normalize_int_columns(df, ["COMPANYCODE", "ID", "JSID"])
    df["COMPANYCODE"] = df["COMPANYCODE"].astype(int)
    df["report_end_date"] = pd.to_datetime(df["report_end_date"])
    df["info_date"] = pd.to_datetime(df["info_date"])
    df["insert_time"] = pd.to_datetime(df["insert_time"])
    for col in ["revenue_yoy", "adjusted_profit_yoy", "roe"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values(
        ["COMPANYCODE", "report_end_date", "info_date", "insert_time", "ID", "JSID"],
        ascending=[True, False, False, False, False, False],
    ).drop_duplicates("COMPANYCODE", keep="first")
    return df[["COMPANYCODE", "report_end_date", "revenue_yoy", "adjusted_profit_yoy", "roe"]]


def fetch_future_returns(connector, stocks: pd.DataFrame, context: dict[str, object]) -> pd.DataFrame:
    if stocks.empty:
        return pd.DataFrame(columns=["INNERCODE", "future_excess_return"])
    ids = sql_int_list(stocks["INNERCODE"])
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
  AND TRADINGDAY IN (DATE('{obs}'), DATE('{exit_date}'))
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


def summarize_pool(pool_name: str, selected: pd.DataFrame, returns: pd.DataFrame) -> dict[str, object]:
    measured = selected.merge(returns, on="INNERCODE", how="inner")
    if measured.empty:
        return {
            "组合类型": pool_name,
            "有效样本数": 0,
            "总市值中位数(亿元)": None,
            "等权平均超额收益(%)": None,
            "超额收益胜率(%)": None,
        }
    return {
        "组合类型": pool_name,
        "有效样本数": int(len(measured)),
        "总市值中位数(亿元)": f"{float(measured['market_cap'].median() / 100000000):.2f}",
        "等权平均超额收益(%)": f"{float(measured['future_excess_return'].mean() * 100):.2f}",
        "超额收益胜率(%)": f"{float((measured['future_excess_return'] > 0).mean() * 100):.2f}",
    }


def build_result(connector, as_of_date: str) -> pd.DataFrame:
    context = fetch_context(connector, as_of_date)
    base = fetch_stock_base(connector, context)
    if base.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    market = fetch_market_snapshot(connector, base, context)
    universe = base.merge(market, on="INNERCODE", how="inner")
    if universe.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    small_cap_count = int(len(universe) * SMALL_CAP_FRACTION)
    small_cap = universe.sort_values(["market_cap", "stock_code"], ascending=[True, True]).head(
        small_cap_count
    )
    factors = fetch_latest_factors(connector, small_cap, context)
    panel = small_cap.merge(factors, on="COMPANYCODE", how="left")

    garp = panel[
        (panel["pe"] > 0)
        & (panel["pe"] <= MAX_PE)
        & (panel["revenue_yoy"] >= MIN_REVENUE_YOY_PERCENT)
        & (panel["adjusted_profit_yoy"] > 0)
    ].copy()
    garp["peg"] = garp["pe"] / garp["adjusted_profit_yoy"]
    garp = garp[garp["peg"] <= MAX_PEG].sort_values(
        ["peg", "stock_code"], ascending=[True, True]
    )
    garp = garp.head(PORTFOLIO_LIMIT).copy()

    value = panel[(panel["pb"] > 0) & (panel["pb"] <= MAX_PB) & (panel["roe"] > 0)].copy()
    value = value.sort_values(["pb", "stock_code"], ascending=[True, True]).head(
        PORTFOLIO_LIMIT
    )

    return_input = pd.concat(
        [garp[["INNERCODE"]], value[["INNERCODE"]]], ignore_index=True
    ).drop_duplicates()
    future_returns = fetch_future_returns(connector, return_input, context)

    result = pd.DataFrame(
        [
            summarize_pool("小市值GARP", garp, future_returns),
            summarize_pool("小市值低估值", value, future_returns),
        ],
        columns=OUTPUT_COLUMNS,
    )
    return result


def write_output(result: pd.DataFrame, output_format: str, output_path: str) -> None:
    if output_format == "json":
        content = json.dumps(result.to_dict(orient="records"), ensure_ascii=False, indent=2)
    else:
        buf = io.StringIO()
        result.to_csv(buf, index=False)
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
    as_of_date = validate_as_of_date(args.as_of_date or canonical_as_of_date())
    if not args.output_path:
        args.output_path = str(Path(__file__).with_name("result.csv"))
    if args.output_format == "json" and args.output_path.endswith(".csv"):
        args.output_format = "csv"

    connector = get_db_connector()
    result = build_result(connector, as_of_date)
    write_output(result, args.output_format, args.output_path)


if __name__ == "__main__":
    main()
