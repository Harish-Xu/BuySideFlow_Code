"""Reference implementation for stock_165: liquid ETF clustering and pruning."""

from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import get_db_connector


LOOKBACK_DAYS = 240
MIN_AVG_TURNOVER = 50_000_000
N_CLUSTERS = 30
RANDOM_STATE = 42
N_INIT = 10
CORRELATION_THRESHOLD = 0.85
QUERY_CHUNK_SIZE = 250

OUTPUT_COLUMNS = [
    "trade_date",
    "fund_code",
    "fund_name",
    "listed_date",
    "avg_turnover_amount_240d",
    "cluster_id",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a liquid, return-distinct exchange-traded ETF pool."
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


def query_df(connector, sql: str, timeout: int = 120) -> pd.DataFrame:
    return connector.execute_sql_to_dataframe(sql, max_rows=0, timeout=timeout)


def integer_chunks(values: pd.Series) -> list[list[int]]:
    unique_values = sorted(pd.to_numeric(values, errors="coerce").dropna().astype(int).unique())
    return [
        unique_values[start : start + QUERY_CHUNK_SIZE]
        for start in range(0, len(unique_values), QUERY_CHUNK_SIZE)
    ]


def fetch_trade_date(connector, as_of_date: str) -> str:
    frame = query_df(
        connector,
        f"""
SELECT MAX(DATE(q.TRADINGDAY)) AS trade_date
FROM qt_fundsperformancehis q
JOIN secumain s
  ON s.INNERCODE = q.INNERCODE
JOIN mf_fundarchives f
  ON f.INNERCODE = s.INNERCODE
WHERE s.SECUCATEGORY = 8
  AND f.TYPE = 4
  AND q.TRADINGDAY < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY)
  AND q.CLOSEPRICE IS NOT NULL
  AND q.CLOSEPRICE > 0;
""",
        timeout=60,
    )
    if frame.empty or pd.isna(frame.loc[0, "trade_date"]):
        raise RuntimeError(f"No ETF trading date found on or before {as_of_date}.")
    return pd.Timestamp(frame.loc[0, "trade_date"]).strftime("%Y-%m-%d")


def fetch_trading_days(connector, trade_date: str) -> pd.DatetimeIndex:
    frame = query_df(
        connector,
        f"""
SELECT trading_day
FROM (
    SELECT
        trading_day,
        ROW_NUMBER() OVER (ORDER BY trading_day DESC) AS rn_desc
    FROM (
        SELECT DISTINCT DATE(TRADINGDATE) AS trading_day
        FROM qt_tradingdaynew
        WHERE IFTRADINGDAY = 1
          AND SECUMARKET IN (83, 90)
          AND TRADINGDATE < DATE_ADD(DATE('{trade_date}'), INTERVAL 1 DAY)
    ) calendar_days
) ranked_days
WHERE rn_desc <= {LOOKBACK_DAYS}
ORDER BY trading_day;
""",
        timeout=60,
    )
    days = pd.DatetimeIndex(pd.to_datetime(frame.get("trading_day"), errors="coerce").dropna())
    if len(days) != LOOKBACK_DAYS:
        raise RuntimeError(f"Expected {LOOKBACK_DAYS} trading days, got {len(days)}.")
    return days


def fetch_etf_universe(connector, trade_date: str) -> pd.DataFrame:
    frame = query_df(
        connector,
        f"""
SELECT
    s.INNERCODE,
    s.SECUCODE AS fund_code,
    COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS fund_name,
    DATE(COALESCE(f.LISTEDDATE, s.LISTEDDATE)) AS listed_date
FROM secumain s
JOIN mf_fundarchives f
  ON f.INNERCODE = s.INNERCODE
WHERE s.SECUCATEGORY = 8
  AND f.TYPE = 4
  AND COALESCE(f.LISTEDDATE, s.LISTEDDATE) IS NOT NULL
  AND DATE(COALESCE(f.LISTEDDATE, s.LISTEDDATE))
      <= DATE_SUB(DATE('{trade_date}'), INTERVAL 3 YEAR)
ORDER BY s.SECUCODE;
""",
        timeout=60,
    )
    if frame.empty:
        raise RuntimeError(f"No exchange-traded ETF was listed for three years by {trade_date}.")
    frame["INNERCODE"] = pd.to_numeric(frame["INNERCODE"], errors="coerce").astype("Int64")
    frame["fund_code"] = frame["fund_code"].astype(str).str.zfill(6)
    frame["listed_date"] = pd.to_datetime(frame["listed_date"], errors="coerce")
    return (
        frame.dropna(subset=["INNERCODE", "listed_date"])
        .drop_duplicates("INNERCODE", keep="first")
        .reset_index(drop=True)
    )


def fetch_liquid_universe(
    connector,
    universe: pd.DataFrame,
    window_start: str,
    window_end: str,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for chunk in integer_chunks(universe["INNERCODE"]):
        innercode_sql = ", ".join(str(value) for value in chunk)
        part = query_df(
            connector,
            f"""
SELECT
    q.INNERCODE,
    COUNT(*) AS row_count,
    COUNT(DISTINCT DATE(q.TRADINGDAY)) AS trading_day_count,
    AVG(q.TURNOVERVALUE) AS avg_turnover_amount_240d
FROM qt_fundsperformancehis q FORCE INDEX (IX_QT_FUNDSPERFORMANCEHIS)
WHERE q.INNERCODE IN ({innercode_sql})
  AND q.TRADINGDAY >= DATE('{window_start}')
  AND q.TRADINGDAY < DATE_ADD(DATE('{window_end}'), INTERVAL 1 DAY)
  AND q.CLOSEPRICE IS NOT NULL
  AND q.CLOSEPRICE > 0
  AND q.PREVCLOSEPRICE IS NOT NULL
  AND q.PREVCLOSEPRICE > 0
  AND q.TURNOVERVALUE IS NOT NULL
GROUP BY q.INNERCODE
HAVING COUNT(*) = {LOOKBACK_DAYS}
   AND COUNT(DISTINCT DATE(q.TRADINGDAY)) = {LOOKBACK_DAYS}
   AND AVG(q.TURNOVERVALUE) > {MIN_AVG_TURNOVER};
""",
            timeout=90,
        )
        if not part.empty:
            frames.append(part)
    if not frames:
        raise RuntimeError("No ETF satisfies the 240-day completeness and liquidity filters.")

    stats = pd.concat(frames, ignore_index=True)
    stats["INNERCODE"] = pd.to_numeric(stats["INNERCODE"], errors="coerce").astype("Int64")
    stats["avg_turnover_amount_240d"] = pd.to_numeric(
        stats["avg_turnover_amount_240d"], errors="coerce"
    )
    stats = stats.dropna(subset=["INNERCODE", "avg_turnover_amount_240d"])
    return universe.merge(
        stats[["INNERCODE", "avg_turnover_amount_240d"]],
        on="INNERCODE",
        how="inner",
    )


def fetch_close_prices(
    connector,
    liquid_universe: pd.DataFrame,
    window_start: str,
    window_end: str,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for chunk in integer_chunks(liquid_universe["INNERCODE"]):
        innercode_sql = ", ".join(str(value) for value in chunk)
        part = query_df(
            connector,
            f"""
SELECT
    q.INNERCODE,
    DATE(q.TRADINGDAY) AS trading_day,
    q.CLOSEPRICE AS close_price,
    q.PREVCLOSEPRICE AS previous_close_price
FROM qt_fundsperformancehis q FORCE INDEX (IX_QT_FUNDSPERFORMANCEHIS)
WHERE q.INNERCODE IN ({innercode_sql})
  AND q.TRADINGDAY >= DATE('{window_start}')
  AND q.TRADINGDAY < DATE_ADD(DATE('{window_end}'), INTERVAL 1 DAY)
  AND q.CLOSEPRICE IS NOT NULL
  AND q.CLOSEPRICE > 0
  AND q.PREVCLOSEPRICE IS NOT NULL
  AND q.PREVCLOSEPRICE > 0;
""",
            timeout=90,
        )
        if not part.empty:
            frames.append(part)
    if not frames:
        raise RuntimeError("No close prices returned for the liquid ETF universe.")

    prices = pd.concat(frames, ignore_index=True)
    prices["INNERCODE"] = pd.to_numeric(prices["INNERCODE"], errors="coerce").astype("Int64")
    prices["trading_day"] = pd.to_datetime(prices["trading_day"], errors="coerce")
    prices["close_price"] = pd.to_numeric(prices["close_price"], errors="coerce")
    prices["previous_close_price"] = pd.to_numeric(
        prices["previous_close_price"], errors="coerce"
    )
    prices = prices.dropna(
        subset=["INNERCODE", "trading_day", "close_price", "previous_close_price"]
    )
    if prices.duplicated(["INNERCODE", "trading_day"]).any():
        raise RuntimeError("Duplicate ETF close prices found for a trading day.")
    return prices.sort_values(["INNERCODE", "trading_day"]).reset_index(drop=True)


def representative_rank(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.sort_values(["listed_date", "fund_code"], ascending=[True, True])


def connected_components(codes: list[str], corr: pd.DataFrame) -> list[list[str]]:
    parent = {code: code for code in codes}

    def find(code: str) -> str:
        while parent[code] != code:
            parent[code] = parent[parent[code]]
            code = parent[code]
        return code

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for index, left in enumerate(codes):
        for right in codes[index + 1 :]:
            value = corr.loc[left, right]
            if pd.notna(value) and value > CORRELATION_THRESHOLD:
                union(left, right)

    grouped: dict[str, list[str]] = {}
    for code in codes:
        grouped.setdefault(find(code), []).append(code)
    return list(grouped.values())


def build_result(
    liquid_universe: pd.DataFrame,
    prices: pd.DataFrame,
    trade_date: str,
    trading_days: pd.DatetimeIndex,
) -> pd.DataFrame:
    prices = prices.merge(
        liquid_universe[["INNERCODE", "fund_code"]],
        on="INNERCODE",
        how="inner",
    )
    price_matrix = (
        prices.pivot(index="trading_day", columns="fund_code", values="close_price")
        .reindex(trading_days)
        .sort_index(axis=1)
    )
    if price_matrix.shape[0] != LOOKBACK_DAYS or price_matrix.isna().any().any():
        raise RuntimeError("ETF close-price matrix is not complete over 240 trading days.")

    # Official previous close is adjusted for ETF share splits/conversions.  A
    # direct ratio of adjacent raw closes would misclassify those mechanical
    # price jumps as investment returns.
    prices["daily_return"] = prices["close_price"] / prices["previous_close_price"] - 1
    returns = (
        prices.pivot(index="trading_day", columns="fund_code", values="daily_return")
        .reindex(trading_days)
        .sort_index(axis=1)
        .iloc[1:]
    )
    returns = returns.replace([np.inf, -np.inf], np.nan).dropna(axis=1, how="any")
    if returns.shape[0] != LOOKBACK_DAYS - 1 or returns.shape[1] < N_CLUSTERS:
        raise RuntimeError("Not enough valid ETF return sequences for 30-cluster KMeans.")

    info = (
        liquid_universe[
            ["fund_code", "fund_name", "listed_date", "avg_turnover_amount_240d"]
        ]
        .drop_duplicates("fund_code", keep="first")
        .set_index("fund_code")
        .loc[returns.columns]
    )
    labels = KMeans(
        n_clusters=N_CLUSTERS,
        random_state=RANDOM_STATE,
        n_init=N_INIT,
    ).fit_predict(returns.T.to_numpy(dtype=float))
    label_series = pd.Series(labels, index=returns.columns, name="cluster_id")

    representatives: list[dict[str, object]] = []
    for cluster_id in sorted(label_series.unique()):
        codes = label_series[label_series == cluster_id].index
        representative = representative_rank(info.loc[codes].reset_index()).iloc[0]
        representatives.append(
            {
                "trade_date": trade_date,
                "fund_code": representative["fund_code"],
                "fund_name": representative["fund_name"],
                "listed_date": representative["listed_date"],
                "avg_turnover_amount_240d": float(
                    representative["avg_turnover_amount_240d"]
                ),
                "cluster_id": int(cluster_id),
            }
        )

    representative_frame = pd.DataFrame(representatives)
    representative_codes = representative_frame["fund_code"].tolist()
    corr = returns[representative_codes].corr()
    selected_codes: set[str] = set()
    representative_info = representative_frame.set_index("fund_code")
    for component in connected_components(representative_codes, corr):
        component_info = representative_rank(
            representative_info.loc[component].reset_index()
        )
        selected_codes.add(str(component_info.iloc[0]["fund_code"]))

    return (
        representative_frame[representative_frame["fund_code"].isin(selected_codes)]
        .sort_values("cluster_id")
        .reset_index(drop=True)[OUTPUT_COLUMNS]
    )


def compute_result(connector, as_of_date: str) -> pd.DataFrame:
    trade_date = fetch_trade_date(connector, as_of_date)
    trading_days = fetch_trading_days(connector, trade_date)
    window_start = trading_days[0].strftime("%Y-%m-%d")
    window_end = trading_days[-1].strftime("%Y-%m-%d")
    universe = fetch_etf_universe(connector, trade_date)
    liquid_universe = fetch_liquid_universe(
        connector, universe, window_start, window_end
    )
    prices = fetch_close_prices(
        connector, liquid_universe, window_start, window_end
    )
    return build_result(liquid_universe, prices, trade_date, trading_days)


def format_result(result: pd.DataFrame) -> pd.DataFrame:
    formatted = result.copy()
    if formatted.empty:
        return formatted.reindex(columns=OUTPUT_COLUMNS)
    formatted["trade_date"] = pd.to_datetime(formatted["trade_date"]).dt.strftime("%Y-%m-%d")
    formatted["fund_code"] = formatted["fund_code"].astype(str).str.zfill(6)
    formatted["listed_date"] = pd.to_datetime(formatted["listed_date"]).dt.strftime("%Y-%m-%d")
    formatted["avg_turnover_amount_240d"] = pd.to_numeric(
        formatted["avg_turnover_amount_240d"], errors="coerce"
    ).round(4)
    formatted["cluster_id"] = formatted["cluster_id"].astype(int)
    return formatted[OUTPUT_COLUMNS]


def write_output(result: pd.DataFrame, output_format: str, output_path: str) -> None:
    formatted = format_result(result)
    if output_format == "json":
        content = json.dumps(formatted.to_dict(orient="records"), ensure_ascii=False, indent=2)
    else:
        buffer = io.StringIO()
        formatted.to_csv(buffer, index=False, float_format="%.4f", lineterminator="\r\n")
        content = buffer.getvalue()

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
