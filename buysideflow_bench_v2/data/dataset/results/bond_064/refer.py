#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import get_db_connector  # noqa: E402


QUERY_TIMEOUT = 300
AS_OF_DATE = "2025-04-30"
HISTORY_START_DATE = "2024-01-01"
MIN_VALID_DAYS = 60
PERCENTILE_THRESHOLD = 60.0
MIN_TRADE_DAYS_20 = 1
CHUNK_SIZE = 500
OUTPUT_COLUMNS = [
    "债券代码",
    "债券简称",
    "发行人",
    "区域",
    "长期债项评级",
    "剩余期限",
    "当前估值收益率(%)",
    "收益率历史分位(%)",
    "近20日成交天数",
]


def split_sql_script(script: str) -> list[str]:
    cleaned_lines = []
    for line in script.splitlines():
        if line.lstrip().startswith("--"):
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines)
    return [stmt.strip() for stmt in cleaned.split(";") if stmt.strip()]


def empty_result() -> pd.DataFrame:
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def sql_num_list(values) -> str:
    return ",".join(str(int(value)) for value in values)


def chunks(values, size: int = CHUNK_SIZE):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def execute_current_candidate_script(conn, connector) -> pd.DataFrame:
    sql_path = Path(__file__).with_name("refer.sql")
    statements = split_sql_script(sql_path.read_text(encoding="utf-8"))
    if not statements:
        return empty_result()

    result = pd.DataFrame()
    for statement in statements:
        result = connector.execute_sql_to_dataframe_on_connection(
            conn,
            statement,
            max_rows=0,
            timeout=QUERY_TIMEOUT,
        )
    return result


def fetch_history_stats(conn, connector, current: pd.DataFrame) -> pd.DataFrame:
    if current.empty:
        return pd.DataFrame(columns=["MAINCODE", "valid_valuation_days", "historical_percentile_pct"])

    current_rows = current[["MAINCODE", "INNERCODE", "VPYIELD"]].copy()
    current_rows["MAINCODE"] = pd.to_numeric(current_rows["MAINCODE"], errors="coerce")
    current_rows["INNERCODE"] = pd.to_numeric(current_rows["INNERCODE"], errors="coerce")
    current_rows["VPYIELD"] = pd.to_numeric(current_rows["VPYIELD"], errors="coerce")
    current_rows = current_rows.dropna()

    frames: list[pd.DataFrame] = []
    for _, batch in current_rows.groupby(current_rows.index // 300):
        values_sql = "\nUNION ALL\n".join(
            "SELECT {maincode} AS MAINCODE, {innercode} AS INNERCODE, {yield_value:.10f} AS current_yield".format(
                maincode=int(row.MAINCODE),
                innercode=int(row.INNERCODE),
                yield_value=float(row.VPYIELD),
            )
            for row in batch.itertuples(index=False)
        )
        sql = f"""
        WITH current_batch AS (
            {values_sql}
        ),
        daily_history AS (
            SELECT MAINCODE, current_yield, valuation_date, VPYIELD
            FROM (
                SELECT
                    cb.MAINCODE,
                    cb.current_yield,
                    DATE(v.ENDDATE) AS valuation_date,
                    v.VPYIELD,
                    ROW_NUMBER() OVER (
                        PARTITION BY cb.MAINCODE, DATE(v.ENDDATE)
                        ORDER BY v.UPDATETIME DESC, v.ID DESC
                    ) AS rn
                FROM current_batch cb
                JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL)
                  ON v.INNERCODE = cb.INNERCODE
                WHERE v.ENDDATE >= DATE('{HISTORY_START_DATE}')
                  AND v.ENDDATE < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY)
                  AND v.CREDIBILITYCODE = 1
                  AND v.INSERTTIME < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY)
            ) ranked
            WHERE rn = 1 AND VPYIELD IS NOT NULL
        )
        SELECT
            MAINCODE,
            COUNT(*) AS valid_valuation_days,
            CASE
                WHEN COUNT(*) > 0
                THEN SUM(CASE WHEN VPYIELD <= current_yield THEN 1 ELSE 0 END)
                     / COUNT(*) * 100
                ELSE 0
            END AS historical_percentile_pct
        FROM daily_history
        GROUP BY MAINCODE;
        """
        frame = connector.execute_sql_to_dataframe_on_connection(conn, sql, max_rows=0, timeout=QUERY_TIMEOUT)
        if not frame.empty:
            frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=["MAINCODE", "valid_valuation_days", "historical_percentile_pct"])

    stats = pd.concat(frames, ignore_index=True)
    stats["MAINCODE"] = pd.to_numeric(stats["MAINCODE"], errors="coerce").astype("Int64")
    stats["valid_valuation_days"] = pd.to_numeric(stats["valid_valuation_days"], errors="coerce").fillna(0).astype(int)
    stats["historical_percentile_pct"] = pd.to_numeric(stats["historical_percentile_pct"], errors="coerce")
    return stats


def fetch_trade20(conn, connector, maincodes: list[int]) -> pd.DataFrame:
    if not maincodes:
        return pd.DataFrame(columns=["MAINCODE", "trade_days_20"])

    date_sql = f"""
    SELECT DATE(TRADINGDATE) AS trade_date
    FROM (
        SELECT DISTINCT TRADINGDATE
        FROM qt_tradingdaynew
        WHERE TRADINGDATE <= DATE('{AS_OF_DATE}')
          AND IFTRADINGDAY = 1
          AND SECUMARKET IN (18, 83, 89, 90)
        ORDER BY TRADINGDATE DESC
        LIMIT 20
    ) d;
    """
    dates = connector.execute_sql_to_dataframe_on_connection(conn, date_sql, max_rows=0, timeout=QUERY_TIMEOUT)
    if dates.empty:
        return pd.DataFrame(columns=["MAINCODE", "trade_days_20"])
    date_list = ",".join(f"'{pd.Timestamp(value).date().isoformat()}'" for value in dates["trade_date"])

    frames: list[pd.DataFrame] = []
    for part in chunks(maincodes, 500):
        # Quote SECUMARKET identifies a venue, not the listing-market enum.
        sql = f"""
        SELECT
            bc.MAINCODE,
            COUNT(DISTINCT DATE(q.TRADINGDAY)) AS trade_days_20
        FROM bond_code bc
        JOIN bond_basicquote q FORCE INDEX (IX_BOND_BASICQUOTE)
          ON q.INNERCODE = bc.INNERCODE
        WHERE bc.MAINCODE IN ({sql_num_list(part)})
          AND bc.SECUMARKET IN (18, 83, 89, 90)
          AND q.TRADINGDAY IN ({date_list})
          AND q.INSERTTIME < DATE_ADD(DATE('{AS_OF_DATE}'), INTERVAL 1 DAY)
          AND q.IFTRADING = 1
        GROUP BY bc.MAINCODE;
        """
        frame = connector.execute_sql_to_dataframe_on_connection(conn, sql, max_rows=0, timeout=QUERY_TIMEOUT)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["MAINCODE", "trade_days_20"])

    trade = pd.concat(frames, ignore_index=True)
    trade["MAINCODE"] = pd.to_numeric(trade["MAINCODE"], errors="coerce").astype("Int64")
    trade["trade_days_20"] = pd.to_numeric(trade["trade_days_20"], errors="coerce").fillna(0).astype(int)
    return trade.groupby("MAINCODE", as_index=False)["trade_days_20"].max()


def build_result(current: pd.DataFrame, stats: pd.DataFrame, trade20: pd.DataFrame) -> pd.DataFrame:
    if current.empty or stats.empty:
        return empty_result()

    current = current.copy()
    for col in ["MAINCODE", "INNERCODE", "SECUMARKET"]:
        current[col] = pd.to_numeric(current[col], errors="coerce").astype("Int64")
    current["VPYIELD"] = pd.to_numeric(current["VPYIELD"], errors="coerce")
    current["TRUEREMAINMATURITY"] = pd.to_numeric(current["TRUEREMAINMATURITY"], errors="coerce")

    stats = stats[
        (stats["valid_valuation_days"] >= MIN_VALID_DAYS)
        & (stats["historical_percentile_pct"] >= PERCENTILE_THRESHOLD)
    ].copy()
    if stats.empty:
        return empty_result()

    stats["MAINCODE"] = pd.to_numeric(stats["MAINCODE"], errors="coerce").astype("Int64")
    result = current.merge(stats, on="MAINCODE", how="inner")
    if not trade20.empty:
        result = result.merge(trade20, on="MAINCODE", how="left")
    else:
        result["trade_days_20"] = 0
    result["trade_days_20"] = pd.to_numeric(result["trade_days_20"], errors="coerce").fillna(0).astype(int)
    result = result[result["trade_days_20"] >= MIN_TRADE_DAYS_20].copy()
    if result.empty:
        return empty_result()

    result = result.rename(
        columns={
            "SECUCODE": "债券代码",
            "SECUABBR": "债券简称",
            "ISSUER": "发行人",
            "REGION": "区域",
            "CRDESC": "长期债项评级",
            "TRUEREMAINMATURITY": "剩余期限",
            "VPYIELD": "当前估值收益率(%)",
            "historical_percentile_pct": "收益率历史分位(%)",
            "trade_days_20": "近20日成交天数",
        }
    )

    result = result.sort_values(
        ["收益率历史分位(%)", "当前估值收益率(%)", "债券代码"],
        ascending=[False, False, True],
    )
    for col in ["剩余期限", "当前估值收益率(%)", "收益率历史分位(%)"]:
        result[col] = pd.to_numeric(result[col], errors="coerce").round(4)
    result["近20日成交天数"] = pd.to_numeric(result["近20日成交天数"], errors="coerce").fillna(0).astype(int)

    result = result[OUTPUT_COLUMNS]
    return result.reset_index(drop=True)


def write_result(result: pd.DataFrame) -> pd.DataFrame:
    out_path = Path(__file__).with_name("result.csv")
    result.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


def main() -> pd.DataFrame:
    connector = get_db_connector()
    with connector.connection() as conn:
        current = execute_current_candidate_script(conn, connector)
        stats = fetch_history_stats(conn, connector, current)
        selected_maincodes = sorted(
            set(
                pd.to_numeric(
                    stats[
                        (stats["valid_valuation_days"] >= MIN_VALID_DAYS)
                        & (stats["historical_percentile_pct"] >= PERCENTILE_THRESHOLD)
                    ]["MAINCODE"],
                    errors="coerce",
                ).dropna().astype(int)
            )
        )
        trade20 = fetch_trade20(conn, connector, selected_maincodes)
    return build_result(current, stats, trade20)


if __name__ == "__main__":
    write_result(main())
