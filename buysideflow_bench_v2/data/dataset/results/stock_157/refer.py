"""Reference implementation for stock_157: non-STAR CSI 800 representatives."""

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


LOOKBACK_DAYS = 243
N_CLUSTERS = 7
N_REPRESENTATIVES = 5
RANDOM_STATE = 42
N_INIT = 20
TURNOVER_QUERY_CHUNK_SIZE = 350
PRICE_QUERY_TRADING_DAY_CHUNK_SIZE = 30
ST_RISK_ALERT_TYPES = "1, 5, 12, 14"

OUTPUT_COLUMNS = [
    "rebalance_date",
    "cluster_label",
    "stock_code",
    "stock_name",
    "avg_turnover_amount_243d",
]


def canonical_as_of_date() -> str:
    from sweagent.text2sql import db_connector
    root = Path(db_connector.__file__).resolve().parents[2]
    with (root / "data/dataset/question/dataset.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            task = json.loads(line)
            if task["instance_id"] == "stock_157":
                return validate_as_of_date(task["inputs"]["as_of_date"])
    raise ValueError("stock_157 missing from canonical dataset")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build CSI 800 stock cluster representatives.")
    parser.add_argument("--as-of-date", default=None)
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


def integer_chunks(values: pd.Series, size: int) -> list[list[int]]:
    unique_values = sorted(pd.to_numeric(values, errors="coerce").dropna().astype(int).unique())
    return [unique_values[start : start + size] for start in range(0, len(unique_values), size)]


def fetch_rebalance_date(connector, as_of_date: str) -> str:
    frame = query_df(
        connector,
        f"""
SELECT MAX(DATE(TRADINGDATE)) AS rebalance_date
FROM qt_tradingdaynew
WHERE IFTRADINGDAY = 1
  AND SECUMARKET IN (83, 90)
  AND TRADINGDATE < DATE_ADD(DATE('{as_of_date}'), INTERVAL 1 DAY);
""",
        timeout=60,
    )
    if frame.empty or pd.isna(frame.loc[0, "rebalance_date"]):
        raise ValueError(f"No A-share trading day found on or before {as_of_date}.")
    return pd.Timestamp(frame.loc[0, "rebalance_date"]).strftime("%Y-%m-%d")


def fetch_trading_days(connector, rebalance_date: str) -> pd.DatetimeIndex:
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
          AND TRADINGDATE < DATE_ADD(DATE('{rebalance_date}'), INTERVAL 1 DAY)
    ) calendar_days
) ranked_days
WHERE rn_desc <= {LOOKBACK_DAYS}
ORDER BY trading_day;
""",
        timeout=60,
    )
    days = pd.DatetimeIndex(pd.to_datetime(frame.get("trading_day"), errors="coerce").dropna())
    if len(days) != LOOKBACK_DAYS:
        raise ValueError(f"Expected {LOOKBACK_DAYS} trading days, got {len(days)}.")
    return days


def fetch_stock_universe(connector, rebalance_date: str) -> pd.DataFrame:
    next_day = (pd.Timestamp(rebalance_date) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    frame = query_df(
        connector,
        f"""
WITH target_index AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '000906'
    ORDER BY INNERCODE
    LIMIT 1
), components AS (
    SELECT DISTINCT ic.SECUINNERCODE AS INNERCODE
    FROM lc_indexcomponent ic
    JOIN target_index ti
      ON ti.INNERCODE = ic.INDEXINNERCODE
    WHERE ic.INDATE <= DATE('{rebalance_date}')
      AND (ic.OUTDATE IS NULL OR ic.OUTDATE > DATE('{rebalance_date}'))
)
SELECT
    s.INNERCODE,
    s.SECUCODE AS stock_code,
    COALESCE(s.CHINAMEABBR, s.SECUABBR, s.CHINAME, s.SECUCODE) AS stock_name,
    EXISTS (
        SELECT 1 FROM lc_suspendresumption sr
        WHERE sr.INNERCODE = s.INNERCODE
          AND sr.SUSPENDDATE < DATE('{next_day}')
          AND (sr.RESUMPTIONDATE IS NULL OR sr.RESUMPTIONDATE >= DATE('{next_day}'))
          AND sr.INFOPUBLDATE IS NULL AND sr.INSERTTIME IS NULL
    ) AS availability_unknown
FROM components c
JOIN secumain s
  ON s.INNERCODE = c.INNERCODE
WHERE s.SECUCATEGORY = 1
  AND s.SECUMARKET IN (83, 90)
  AND s.LISTEDSECTOR <> 7
  AND NOT EXISTS (
      SELECT 1
      FROM cs_riskalert ra
      WHERE ra.INNERCODE = s.INNERCODE
        AND ra.RISKALERTTYPE IN ({ST_RISK_ALERT_TYPES})
        AND ra.IMPLEMENTDATE < DATE('{next_day}')
        -- INSERTTIME is schema-labelled publication time, not the effective date.
        AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME) < DATE('{next_day}')
        AND (
            ra.REMOVEDATE IS NULL
            OR ra.REMOVEDATE >= DATE('{next_day}')
            OR (
                ra.REMOVEINFOPUBLDATE IS NOT NULL
                AND ra.REMOVEINFOPUBLDATE >= DATE('{next_day}')
            )
        )
  )
  AND NOT EXISTS (
      SELECT 1
      FROM lc_suspendresumption sr
      WHERE sr.INNERCODE = s.INNERCODE
        AND sr.SUSPENDDATE < DATE('{next_day}')
        AND COALESCE(sr.INFOPUBLDATE, sr.INSERTTIME) < DATE('{next_day}')
        AND (sr.RESUMPTIONDATE IS NULL OR sr.RESUMPTIONDATE >= DATE('{next_day}'))
  )
ORDER BY s.SECUCODE;
""",
        timeout=120,
    )
    if frame.empty:
        raise ValueError(f"No eligible CSI 800 stocks found on {rebalance_date}.")
    if frame["availability_unknown"].astype(bool).any():
        raise ValueError("Suspension publication availability is unresolved; unknown status must not enter the universe")
    frame = frame.drop(columns="availability_unknown")
    frame["INNERCODE"] = pd.to_numeric(frame["INNERCODE"], errors="coerce").astype("Int64")
    frame["stock_code"] = frame["stock_code"].astype(str).str.zfill(6)
    return frame.dropna(subset=["INNERCODE"]).drop_duplicates("INNERCODE").reset_index(drop=True)


def fetch_adjusted_prices(
    connector,
    universe: pd.DataFrame,
    trading_days: pd.DatetimeIndex,
) -> pd.DataFrame:
    code_sql = ", ".join(str(value) for value in universe["INNERCODE"].astype(int))
    frames: list[pd.DataFrame] = []
    for start in range(0, len(trading_days), PRICE_QUERY_TRADING_DAY_CHUNK_SIZE):
        day_chunk = trading_days[start : start + PRICE_QUERY_TRADING_DAY_CHUNK_SIZE]
        window_start = day_chunk[0].strftime("%Y-%m-%d")
        window_end = day_chunk[-1].strftime("%Y-%m-%d")
        part = query_df(
            connector,
            f"""
SELECT
    INNERCODE,
    DATE(TRADINGDAY) AS trading_day,
    BACKWARDPRICE AS adjusted_close
FROM qt_performancedata FORCE INDEX (IDX_QTPD_TRADINGDAY_INNERCODE)
WHERE TRADINGDAY >= DATE('{window_start}')
  AND TRADINGDAY < DATE_ADD(DATE('{window_end}'), INTERVAL 1 DAY)
  AND INNERCODE IN ({code_sql})
  AND BACKWARDPRICE IS NOT NULL
  AND BACKWARDPRICE > 0;
""",
            timeout=120,
        )
        if not part.empty:
            frames.append(part)
    if not frames:
        raise ValueError("No backward-adjusted prices returned for the CSI 800 universe.")
    frame = pd.concat(frames, ignore_index=True)
    frame["INNERCODE"] = pd.to_numeric(frame["INNERCODE"], errors="coerce").astype("Int64")
    frame["trading_day"] = pd.to_datetime(frame["trading_day"], errors="coerce")
    frame["adjusted_close"] = pd.to_numeric(frame["adjusted_close"], errors="coerce")
    frame = frame.dropna(subset=["INNERCODE", "trading_day", "adjusted_close"])
    if frame.duplicated(["INNERCODE", "trading_day"]).any():
        raise ValueError("Duplicate backward-adjusted prices found for a stock trading day.")
    return frame.sort_values(["INNERCODE", "trading_day"]).reset_index(drop=True)


def fetch_turnover(
    connector,
    universe: pd.DataFrame,
    window_start: str,
    window_end: str,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for chunk in integer_chunks(universe["INNERCODE"], TURNOVER_QUERY_CHUNK_SIZE):
        code_sql = ", ".join(str(value) for value in chunk)
        part = query_df(
            connector,
            f"""
SELECT
    INNERCODE,
    COUNT(DISTINCT CASE WHEN TURNOVERVALUE IS NOT NULL THEN DATE(TRADINGDAY) END)
        AS trading_day_count,
    AVG(TURNOVERVALUE) AS avg_turnover_amount_243d
FROM qt_dailyquote FORCE INDEX (IX_QT_DAILYQUOTE)
WHERE INNERCODE IN ({code_sql})
  AND TRADINGDAY >= DATE('{window_start}')
  AND TRADINGDAY < DATE_ADD(DATE('{window_end}'), INTERVAL 1 DAY)
  AND TURNOVERVALUE IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM qt_tradingdaynew td
      WHERE td.TRADINGDATE = qt_dailyquote.TRADINGDAY
        AND td.IFTRADINGDAY = 1 AND td.SECUMARKET IN (83, 90)
  )
GROUP BY INNERCODE;
""",
            timeout=120,
        )
        if not part.empty:
            frames.append(part)
    if not frames:
        raise ValueError("No turnover records returned for the CSI 800 universe.")
    frame = pd.concat(frames, ignore_index=True)
    frame["INNERCODE"] = pd.to_numeric(frame["INNERCODE"], errors="coerce").astype("Int64")
    frame["trading_day_count"] = pd.to_numeric(
        frame["trading_day_count"], errors="coerce"
    ).astype("Int64")
    frame["avg_turnover_amount_243d"] = pd.to_numeric(
        frame["avg_turnover_amount_243d"], errors="coerce"
    )
    return frame.dropna().drop_duplicates("INNERCODE").reset_index(drop=True)


def fetch_stock_panel(connector, as_of_date: str) -> pd.DataFrame:
    rebalance_date = fetch_rebalance_date(connector, as_of_date)
    trading_days = fetch_trading_days(connector, rebalance_date)
    universe = fetch_stock_universe(connector, rebalance_date)
    window_start = trading_days[0].strftime("%Y-%m-%d")
    window_end = trading_days[-1].strftime("%Y-%m-%d")

    prices = fetch_adjusted_prices(connector, universe, trading_days)
    turnover = fetch_turnover(connector, universe, window_start, window_end)
    complete_turnover_codes = turnover.loc[
        turnover["trading_day_count"] == LOOKBACK_DAYS, "INNERCODE"
    ]
    prices = prices[prices["INNERCODE"].isin(complete_turnover_codes)].copy()
    panel = prices[prices["trading_day"].isin(trading_days)].copy()
    complete_counts = panel.groupby("INNERCODE")["trading_day"].nunique()
    complete_codes = complete_counts[complete_counts == LOOKBACK_DAYS].index
    panel = panel[panel["INNERCODE"].isin(complete_codes)].copy()
    if panel.empty:
        raise ValueError("No stock has a complete 243-trading-day price and turnover window.")

    panel = panel.merge(universe, on="INNERCODE", how="inner")
    panel = panel.merge(
        turnover[["INNERCODE", "avg_turnover_amount_243d"]],
        on="INNERCODE",
        how="inner",
    )
    panel["rebalance_date"] = pd.Timestamp(rebalance_date)
    return panel.sort_values(["stock_code", "trading_day"]).reset_index(drop=True)


def remap_cluster_labels(
    raw_labels: np.ndarray,
    stock_codes: pd.Index,
    log_returns: pd.DataFrame,
) -> dict[int, int]:
    labels = pd.Series(raw_labels, index=stock_codes)
    metrics: list[tuple[int, float]] = []
    for raw_label, codes in labels.groupby(labels).groups.items():
        daily_volatility = log_returns.loc[:, list(codes)].std(axis=0, ddof=0)
        metrics.append((int(raw_label), float(daily_volatility.mean())))
    metrics.sort(key=lambda item: (item[1], item[0]))
    return {raw_label: mapped_label for mapped_label, (raw_label, _) in enumerate(metrics)}


def build_result(stock_panel: pd.DataFrame) -> pd.DataFrame:
    rebalance_dates = pd.to_datetime(stock_panel["rebalance_date"]).dropna().unique()
    if len(rebalance_dates) != 1:
        raise ValueError("Expected exactly one rebalance date.")
    rebalance_date = pd.Timestamp(rebalance_dates[0]).strftime("%Y-%m-%d")

    info = (
        stock_panel[["stock_code", "stock_name"]]
        .drop_duplicates("stock_code")
        .set_index("stock_code")
    )
    price_matrix = stock_panel.pivot(
        index="trading_day", columns="stock_code", values="adjusted_close"
    ).sort_index().sort_index(axis=1)
    if price_matrix.shape[0] != LOOKBACK_DAYS or price_matrix.isna().any().any():
        raise ValueError("Adjusted-price matrix is not complete over 243 trading days.")

    log_returns = np.log(price_matrix).diff().dropna()
    features = log_returns.T.to_numpy(dtype=float)
    row_std = features.std(axis=1, ddof=0)
    valid = np.isfinite(features).all(axis=1) & np.isfinite(row_std) & (row_std > 0)
    if valid.sum() < N_CLUSTERS:
        raise ValueError("Not enough valid stocks for seven KMeans clusters.")
    stock_codes = log_returns.columns[valid]
    features = features[valid]
    standardized = (features - features.mean(axis=1, keepdims=True)) / row_std[valid, None]

    raw_labels = KMeans(
        n_clusters=N_CLUSTERS,
        random_state=RANDOM_STATE,
        n_init=N_INIT,
    ).fit_predict(standardized)
    label_map = remap_cluster_labels(raw_labels, stock_codes, log_returns)
    cluster_labels = pd.Series(
        [label_map[int(label)] for label in raw_labels],
        index=stock_codes,
        name="cluster_label",
    )
    avg_turnover = stock_panel.groupby("stock_code")["avg_turnover_amount_243d"].first()

    rows: list[dict[str, object]] = []
    for cluster_label in sorted(cluster_labels.unique()):
        codes = cluster_labels[cluster_labels == cluster_label].index
        ranked = pd.DataFrame(
            {
                "stock_code": codes,
                "avg_turnover_amount_243d": avg_turnover.reindex(codes).to_numpy(),
            }
        ).sort_values(
            ["avg_turnover_amount_243d", "stock_code"],
            ascending=[False, True],
            kind="stable",
        ).head(N_REPRESENTATIVES)
        for row in ranked.itertuples(index=False):
            rows.append(
                {
                    "rebalance_date": rebalance_date,
                    "cluster_label": int(cluster_label),
                    "stock_code": row.stock_code,
                    "stock_name": info.loc[row.stock_code, "stock_name"],
                    "avg_turnover_amount_243d": float(row.avg_turnover_amount_243d),
                }
            )

    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS).sort_values(
        ["cluster_label", "avg_turnover_amount_243d", "stock_code"],
        ascending=[True, False, True],
        kind="stable",
    ).reset_index(drop=True)


def format_result(result: pd.DataFrame) -> pd.DataFrame:
    formatted = result.copy()
    if formatted.empty:
        return formatted.reindex(columns=OUTPUT_COLUMNS)
    formatted["rebalance_date"] = pd.to_datetime(formatted["rebalance_date"]).dt.strftime(
        "%Y-%m-%d"
    )
    formatted["cluster_label"] = formatted["cluster_label"].astype(int)
    formatted["stock_code"] = formatted["stock_code"].astype(str).str.zfill(6)
    formatted["avg_turnover_amount_243d"] = pd.to_numeric(
        formatted["avg_turnover_amount_243d"], errors="coerce"
    ).round(2)
    return formatted[OUTPUT_COLUMNS]


def write_output(result: pd.DataFrame, output_format: str, output_path: str) -> None:
    formatted = format_result(result)
    if output_format == "json":
        content = json.dumps(formatted.to_dict(orient="records"), ensure_ascii=False, indent=2)
        encoding = "utf-8"
    else:
        buffer = io.StringIO()
        formatted.to_csv(buffer, index=False, float_format="%.2f", lineterminator="\r\n")
        content = buffer.getvalue()
        encoding = "utf-8-sig"

    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding=encoding)
        print(f"Saved output to {path}")
    else:
        print(content)


def main() -> None:
    args = parse_args()
    as_of_date = validate_as_of_date(args.as_of_date or canonical_as_of_date())
    if not args.output_path:
        args.output_path = str(Path(__file__).with_name("result.csv"))
    if args.output_format == "json" and args.output_path.endswith(".csv"):
        args.output_format = "csv"

    connector = get_db_connector()
    try:
        result = build_result(fetch_stock_panel(connector, as_of_date))
        write_output(result, args.output_format, args.output_path)
    finally:
        connector.close_all()


if __name__ == "__main__":
    main()
