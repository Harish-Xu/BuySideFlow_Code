#!/usr/bin/env python3
from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe, shared_connection_session


START_DATE = "2024-01-01"
END_DATE = "2025-01-01"
CALENDAR_END = "2025-04-01"
HOLDING_INTERVALS = 20
EVENT_OUTLOOK_CODES = (1203, 1208, 1209)
EVENT_RATING_CODES = (970, 980)
CREDIT_L1 = (1600, 1700, 1800, 2500, 2600, 2800, 2900, 3100, 3200, 3300, 3700)
CREDIT_L2 = (1901, 1903)
RESULT_COLUMNS = ["有效事件数", "平均相对全价变动", "相对全价变动大于0的事件占比"]


def sql_nums(values) -> str:
    return ",".join(str(int(value)) for value in sorted(set(values)))


def query(sql: str) -> pd.DataFrame:
    frame = query_to_dataframe(sql, max_rows=0, timeout=600)
    if frame is None:
        raise ValueError("Database query returned None")
    return frame


def fetch_events() -> pd.DataFrame:
    sql = f"""
    WITH
    event_days AS (
        SELECT DISTINCT g.MAINCODE, DATE(g.INFOPUBLDATE) AS signal_date
        FROM bond_bdcreditgrading g
        WHERE g.INFOPUBLDATE >= '{START_DATE}'
          AND g.INFOPUBLDATE < '{END_DATE}'
          AND g.CRTYPECODE = 2
          AND g.CRSYSTEM = 1
          AND g.CROUTLOOK IN ({sql_nums(EVENT_OUTLOOK_CODES)})
          AND g.CRSTATUS = 1
          AND g.MAINCODE IS NOT NULL
    ),
    event_terms AS (
        SELECT
            e.MAINCODE,
            e.signal_date,
            MAX(DATE(bc.INTERESTENDDATE)) AS maturity_date
        FROM event_days e
        JOIN bond_code bc ON bc.MAINCODE = e.MAINCODE
        WHERE (
                bc.BONDTYPELEVEL1 IN ({sql_nums(CREDIT_L1)})
             OR bc.BONDTYPELEVEL2 IN ({sql_nums(CREDIT_L2)})
        )
          AND bc.LISTEDDATE <= e.signal_date
          AND bc.INTERESTENDDATE > e.signal_date
          AND (bc.DELISTDATE IS NULL OR bc.DELISTDATE > e.signal_date)
        GROUP BY e.MAINCODE, e.signal_date
    ),
    rating_versions AS (
        SELECT
            e.MAINCODE,
            e.signal_date,
            r.CRASCODE,
            r.CRASNAME,
            r.CRCODE,
            r.CRSTATUS,
            r.RATINGEFFECTIVEENDDATE,
            ROW_NUMBER() OVER (
                PARTITION BY e.MAINCODE, e.signal_date,
                             COALESCE(CAST(r.CRASCODE AS CHAR), r.CRASNAME)
                ORDER BY r.INFOPUBLDATE DESC, r.CRDATE DESC, r.ID DESC
            ) AS version_rank
        FROM event_terms e
        JOIN bond_bdcreditgrading r ON r.MAINCODE = e.MAINCODE
        WHERE r.CRTYPECODE = 2
          AND r.CRSYSTEM = 1
          AND r.CRCODE IS NOT NULL
          AND r.INFOPUBLDATE < DATE_ADD(e.signal_date, INTERVAL 1 DAY)
          AND r.CRDATE < DATE_ADD(e.signal_date, INTERVAL 1 DAY)
    ),
    event_ratings AS (
        SELECT MAINCODE, signal_date, MIN(CRCODE) AS rating_code
        FROM rating_versions
        WHERE version_rank = 1
          AND CRSTATUS = 1
          AND (RATINGEFFECTIVEENDDATE IS NULL OR RATINGEFFECTIVEENDDATE >= signal_date)
        GROUP BY MAINCODE, signal_date
    ),
    qualified AS (
        SELECT
            e.MAINCODE,
            e.signal_date,
            e.maturity_date,
            r.rating_code,
            DATEDIFF(e.maturity_date, e.signal_date) / 365.0 AS remaining_years
        FROM event_terms e
        JOIN event_ratings r
          ON r.MAINCODE = e.MAINCODE
         AND r.signal_date = e.signal_date
        WHERE r.rating_code IN ({sql_nums(EVENT_RATING_CODES)})
          AND DATEDIFF(e.maturity_date, e.signal_date) / 365.0 > 1
          AND DATEDIFF(e.maturity_date, e.signal_date) / 365.0 <= 5
    ),
    first_qualified AS (
        SELECT
            q.*,
            ROW_NUMBER() OVER (
                PARTITION BY q.MAINCODE
                ORDER BY q.signal_date, q.MAINCODE
            ) AS event_rank
        FROM qualified q
    )
    SELECT MAINCODE, signal_date, maturity_date, rating_code, remaining_years
    FROM first_qualified
    WHERE event_rank = 1
    ORDER BY signal_date, MAINCODE
    """
    events = query(sql)
    if events.empty:
        return events
    events = events.copy()
    events["MAINCODE"] = pd.to_numeric(events["MAINCODE"], errors="raise").astype("int64")
    events["rating_code"] = pd.to_numeric(events["rating_code"], errors="raise").astype("int64")
    events["remaining_years"] = pd.to_numeric(events["remaining_years"], errors="raise")
    events["signal_date"] = pd.to_datetime(events["signal_date"])
    return events


def fetch_valuation_calendar() -> pd.DatetimeIndex:
    dates = query(f"""
        SELECT DISTINCT DATE(ENDDATE) AS valuation_date
        FROM bond_cbvaluationall FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
        WHERE ENDDATE >= '{START_DATE}'
          AND ENDDATE < '{CALENDAR_END}'
        ORDER BY valuation_date
    """)
    if dates.empty:
        raise ValueError("Missing ChinaBond valuation calendar")
    return pd.DatetimeIndex(pd.to_datetime(dates["valuation_date"])).normalize().unique().sort_values()


def attach_holding_dates(events: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    rows = []
    for event in events.itertuples(index=False):
        entry_index = calendar.searchsorted(event.signal_date, side="right")
        exit_index = entry_index + HOLDING_INTERVALS
        if entry_index >= len(calendar) or exit_index >= len(calendar):
            continue
        row = event._asdict()
        row["entry_date"] = calendar[entry_index]
        row["exit_date"] = calendar[exit_index]
        entry_years = (pd.Timestamp(event.maturity_date) - row["entry_date"]).days / 365.0
        if not 1 < entry_years <= 5:
            continue
        row["maturity_bucket"] = "1-3Y" if entry_years <= 3 else "3-5Y"
        rows.append(row)
    return pd.DataFrame(rows)


def fetch_entry_ratings(maincodes, entry_date) -> dict[int, int]:
    codes = sorted(set(int(value) for value in maincodes))
    if not codes:
        return {}
    entry = pd.Timestamp(entry_date).date().isoformat()
    frame = query(f"""
        WITH rating_versions AS (
            SELECT r.MAINCODE, r.CRCODE, r.CRSTATUS, r.RATINGEFFECTIVEENDDATE,
                   ROW_NUMBER() OVER (
                       PARTITION BY r.MAINCODE,
                                    COALESCE(CAST(r.CRASCODE AS CHAR), r.CRASNAME)
                       ORDER BY r.INFOPUBLDATE DESC, r.CRDATE DESC, r.ID DESC
                   ) AS version_rank
            FROM bond_bdcreditgrading r
            WHERE r.MAINCODE IN ({sql_nums(codes)})
              AND r.CRTYPECODE = 2 AND r.CRSYSTEM = 1
              AND r.CRCODE IS NOT NULL
              AND r.INFOPUBLDATE < DATE_ADD('{entry}', INTERVAL 1 DAY)
              AND r.CRDATE < DATE_ADD('{entry}', INTERVAL 1 DAY)
        )
        SELECT MAINCODE, MIN(CRCODE) AS rating_code
        FROM rating_versions
        WHERE version_rank = 1 AND CRSTATUS = 1
          AND (RATINGEFFECTIVEENDDATE IS NULL OR RATINGEFFECTIVEENDDATE >= '{entry}')
        GROUP BY MAINCODE
    """)
    return {int(row.MAINCODE): int(row.rating_code) for row in frame.itertuples(index=False)}


def fetch_pair_prices(maincodes, entry_date, exit_date) -> pd.DataFrame:
    codes = sorted(set(int(value) for value in maincodes))
    if not codes:
        return pd.DataFrame()
    entry = pd.Timestamp(entry_date).date().isoformat()
    exit_ = pd.Timestamp(exit_date).date().isoformat()
    sql = f"""
    WITH
    entry_versions AS (
        SELECT
            bc.MAINCODE,
            bc.INNERCODE,
            bc.SECUCODE,
            bc.SECUMARKET,
            v.VALUEFULLPRICE,
            ROW_NUMBER() OVER (
                PARTITION BY bc.INNERCODE
                ORDER BY v.INSERTTIME DESC, v.ID DESC
            ) AS version_rank
        FROM bond_code bc
        JOIN bond_cbvaluationall v ON v.INNERCODE = bc.INNERCODE
        WHERE bc.MAINCODE IN ({sql_nums(codes)})
          AND bc.LISTEDDATE <= '{entry}'
          AND bc.INTERESTENDDATE > '{entry}'
          AND (bc.DELISTDATE IS NULL OR bc.DELISTDATE > '{entry}')
          AND v.ENDDATE >= '{entry}'
          AND v.ENDDATE < DATE_ADD('{entry}', INTERVAL 1 DAY)
          AND v.CREDIBILITYCODE = 1
          AND v.YIELDCODE = 2
          AND v.VALUEFULLPRICE IS NOT NULL
    ),
    selected_codes AS (
        SELECT
            e.*,
            ROW_NUMBER() OVER (
                PARTITION BY e.MAINCODE
                ORDER BY CASE WHEN e.SECUMARKET = 89 THEN 0 ELSE 1 END,
                         e.SECUCODE,
                         e.INNERCODE
            ) AS code_rank
        FROM entry_versions e
        WHERE e.version_rank = 1
    ),
    exit_versions AS (
        SELECT
            s.MAINCODE,
            s.INNERCODE,
            s.SECUCODE,
            s.VALUEFULLPRICE AS entry_price,
            v.VALUEFULLPRICE AS exit_price,
            ROW_NUMBER() OVER (
                PARTITION BY s.INNERCODE
                ORDER BY v.INSERTTIME DESC, v.ID DESC
            ) AS exit_version_rank
        FROM selected_codes s
        JOIN bond_cbvaluationall v ON v.INNERCODE = s.INNERCODE
        WHERE s.code_rank = 1
          AND v.ENDDATE >= '{exit_}'
          AND v.ENDDATE < DATE_ADD('{exit_}', INTERVAL 1 DAY)
          AND v.CREDIBILITYCODE = 1
          AND v.YIELDCODE = 2
          AND v.VALUEFULLPRICE IS NOT NULL
    )
    SELECT MAINCODE, INNERCODE, SECUCODE, entry_price, exit_price
    FROM exit_versions
    WHERE exit_version_rank = 1
    ORDER BY MAINCODE
    """
    frame = query(sql)
    if frame.empty:
        return frame
    frame["MAINCODE"] = pd.to_numeric(frame["MAINCODE"], errors="raise").astype("int64")
    for column in ["entry_price", "exit_price"]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


def fetch_peers(entry_date, exit_date, rating_code: int, maturity_bucket: str) -> pd.DataFrame:
    entry = pd.Timestamp(entry_date).date().isoformat()
    exit_ = pd.Timestamp(exit_date).date().isoformat()
    if maturity_bucket == "1-3Y":
        maturity_filter = "remaining_years > 1 AND remaining_years <= 3"
    elif maturity_bucket == "3-5Y":
        maturity_filter = "remaining_years > 3 AND remaining_years <= 5"
    else:
        raise ValueError(f"Unsupported maturity bucket: {maturity_bucket}")

    sql = f"""
    WITH
    alive_terms AS (
        SELECT
            bc.MAINCODE,
            MAX(DATE(bc.INTERESTENDDATE)) AS maturity_date,
            DATEDIFF(MAX(DATE(bc.INTERESTENDDATE)), '{entry}') / 365.0 AS remaining_years
        FROM bond_code bc
        WHERE bc.MAINCODE IS NOT NULL
          AND (
                bc.BONDTYPELEVEL1 IN ({sql_nums(CREDIT_L1)})
             OR bc.BONDTYPELEVEL2 IN ({sql_nums(CREDIT_L2)})
          )
          AND bc.LISTEDDATE <= '{entry}'
          AND bc.INTERESTENDDATE > '{entry}'
          AND (bc.DELISTDATE IS NULL OR bc.DELISTDATE > '{entry}')
        GROUP BY bc.MAINCODE
    ),
    term_matched AS (
        SELECT * FROM alive_terms WHERE {maturity_filter}
    ),
    rating_versions AS (
        SELECT
            t.MAINCODE,
            r.CRASCODE,
            r.CRASNAME,
            r.CRCODE,
            r.CRSTATUS,
            r.RATINGEFFECTIVEENDDATE,
            ROW_NUMBER() OVER (
                PARTITION BY t.MAINCODE,
                             COALESCE(CAST(r.CRASCODE AS CHAR), r.CRASNAME)
                ORDER BY r.INFOPUBLDATE DESC, r.CRDATE DESC, r.ID DESC
            ) AS version_rank
        FROM term_matched t
        JOIN bond_bdcreditgrading r ON r.MAINCODE = t.MAINCODE
        WHERE r.CRTYPECODE = 2
          AND r.CRSYSTEM = 1
          AND r.CRCODE IS NOT NULL
          AND r.INFOPUBLDATE < DATE_ADD('{entry}', INTERVAL 1 DAY)
          AND r.CRDATE < DATE_ADD('{entry}', INTERVAL 1 DAY)
    ),
    ratings AS (
        SELECT MAINCODE, MIN(CRCODE) AS rating_code
        FROM rating_versions
        WHERE version_rank = 1
          AND CRSTATUS = 1
          AND (RATINGEFFECTIVEENDDATE IS NULL OR RATINGEFFECTIVEENDDATE >= '{entry}')
        GROUP BY MAINCODE
        HAVING MIN(CRCODE) = {int(rating_code)}
    ),
    clean_peers AS (
        SELECT r.MAINCODE
        FROM ratings r
        WHERE NOT EXISTS (
            SELECT 1
            FROM bond_bdcreditgrading warning
            WHERE warning.MAINCODE = r.MAINCODE
              AND warning.CRTYPECODE = 2
              AND warning.CRSYSTEM = 1
              AND warning.CRSTATUS = 1
              AND warning.CROUTLOOK IN ({sql_nums(EVENT_OUTLOOK_CODES)})
              AND warning.INFOPUBLDATE < DATE_ADD('{entry}', INTERVAL 1 DAY)
        )
    ),
    entry_versions AS (
        SELECT
            p.MAINCODE,
            bc.INNERCODE,
            bc.SECUCODE,
            bc.SECUMARKET,
            v.VALUEFULLPRICE,
            ROW_NUMBER() OVER (
                PARTITION BY bc.INNERCODE
                ORDER BY v.INSERTTIME DESC, v.ID DESC
            ) AS version_rank
        FROM clean_peers p
        JOIN bond_code bc ON bc.MAINCODE = p.MAINCODE
        JOIN bond_cbvaluationall v ON v.INNERCODE = bc.INNERCODE
        WHERE bc.LISTEDDATE <= '{entry}'
          AND bc.INTERESTENDDATE > '{entry}'
          AND (bc.DELISTDATE IS NULL OR bc.DELISTDATE > '{entry}')
          AND v.ENDDATE >= '{entry}'
          AND v.ENDDATE < DATE_ADD('{entry}', INTERVAL 1 DAY)
          AND v.CREDIBILITYCODE = 1
          AND v.YIELDCODE = 2
          AND v.VALUEFULLPRICE IS NOT NULL
    ),
    selected_codes AS (
        SELECT
            e.*,
            ROW_NUMBER() OVER (
                PARTITION BY e.MAINCODE
                ORDER BY CASE WHEN e.SECUMARKET = 89 THEN 0 ELSE 1 END,
                         e.SECUCODE,
                         e.INNERCODE
            ) AS code_rank
        FROM entry_versions e
        WHERE e.version_rank = 1
    ),
    exit_versions AS (
        SELECT
            s.MAINCODE,
            s.INNERCODE,
            s.SECUCODE,
            s.VALUEFULLPRICE AS entry_price,
            v.VALUEFULLPRICE AS exit_price,
            ROW_NUMBER() OVER (
                PARTITION BY s.INNERCODE
                ORDER BY v.INSERTTIME DESC, v.ID DESC
            ) AS exit_version_rank
        FROM selected_codes s
        JOIN bond_cbvaluationall v ON v.INNERCODE = s.INNERCODE
        WHERE s.code_rank = 1
          AND v.ENDDATE >= '{exit_}'
          AND v.ENDDATE < DATE_ADD('{exit_}', INTERVAL 1 DAY)
          AND v.CREDIBILITYCODE = 1
          AND v.YIELDCODE = 2
          AND v.VALUEFULLPRICE IS NOT NULL
    )
    SELECT MAINCODE, INNERCODE, SECUCODE, entry_price, exit_price
    FROM exit_versions
    WHERE exit_version_rank = 1
    ORDER BY MAINCODE
    """
    peers = query(sql)
    if peers.empty:
        return peers
    peers["MAINCODE"] = pd.to_numeric(peers["MAINCODE"], errors="raise").astype("int64")
    for column in ["entry_price", "exit_price"]:
        peers[column] = pd.to_numeric(peers[column], errors="raise")
    return peers


def full_price_return(entry_price, exit_price) -> float:
    entry = float(entry_price)
    exit_ = float(exit_price)
    if not math.isfinite(entry) or not math.isfinite(exit_) or entry <= 0 or exit_ <= 0:
        raise ValueError("Full-price endpoints must be finite and positive")
    return exit_ / entry - 1.0


def build_detail(periods: pd.DataFrame) -> pd.DataFrame:
    if periods.empty:
        return pd.DataFrame()
    rows = []
    peer_cache: dict[tuple[pd.Timestamp, pd.Timestamp, int, str], pd.DataFrame] = {}
    for (entry_date, exit_date), group in periods.groupby(["entry_date", "exit_date"], sort=True):
        event_prices = fetch_pair_prices(group["MAINCODE"], entry_date, exit_date).set_index("MAINCODE")
        entry_ratings = fetch_entry_ratings(group["MAINCODE"], entry_date)
        for event in group.itertuples(index=False):
            if event.MAINCODE not in event_prices.index or event.MAINCODE not in entry_ratings:
                continue
            rating_code = entry_ratings[event.MAINCODE]
            key = (entry_date, exit_date, rating_code, event.maturity_bucket)
            if key not in peer_cache:
                peer_cache[key] = fetch_peers(*key)
            peers = peer_cache[key]
            peers = peers[peers["MAINCODE"].ne(event.MAINCODE)]
            if peers.empty:
                continue
            peer_returns = peers.apply(
                lambda row: full_price_return(row["entry_price"], row["exit_price"]), axis=1
            )
            event_price = event_prices.loc[event.MAINCODE]
            if isinstance(event_price, pd.DataFrame):
                raise ValueError(f"Duplicate selected event valuation for {event.MAINCODE}")
            event_return = full_price_return(event_price["entry_price"], event_price["exit_price"])
            peer_median = float(peer_returns.median())
            rows.append(
                {
                    "MAINCODE": event.MAINCODE,
                    "signal_date": event.signal_date,
                    "entry_date": entry_date,
                    "exit_date": exit_date,
                    "rating_code": rating_code,
                    "maturity_bucket": event.maturity_bucket,
                    "event_return": event_return,
                    "peer_median_return": peer_median,
                    "peer_count": len(peer_returns),
                    "relative_change": peer_median - event_return,
                }
            )
    return pd.DataFrame(rows)


def summarize(detail: pd.DataFrame) -> pd.DataFrame:
    if detail.empty:
        return pd.DataFrame(
            [{RESULT_COLUMNS[0]: 0, RESULT_COLUMNS[1]: "", RESULT_COLUMNS[2]: ""}],
            columns=RESULT_COLUMNS,
        )
    values = detail["relative_change"].astype(float)
    return pd.DataFrame(
        [{
            RESULT_COLUMNS[0]: int(len(values)),
            RESULT_COLUMNS[1]: f"{values.mean() * 100:.4f}",
            RESULT_COLUMNS[2]: f"{values.gt(0).mean() * 100:.4f}",
        }],
        columns=RESULT_COLUMNS,
    )


def build_result() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    events = fetch_events()
    periods = attach_holding_dates(events, fetch_valuation_calendar())
    detail = build_detail(periods)
    return summarize(detail), detail, periods


def main() -> pd.DataFrame:
    with shared_connection_session():
        result, _, _ = build_result()
    result.to_csv(Path(__file__).with_name("result.csv"), index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
