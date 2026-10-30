#!/usr/bin/env python3
"""Q4 month-end high-valuation-yield portfolios; full-price changes, not total returns."""
from pathlib import Path
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from sweagent.text2sql.db_connector import query_to_dataframe, shared_connection_session

START_DATE = "2024-10-01"
SIGNAL_END_EXCLUSIVE = "2025-01-01"
TRADE_HISTORY_START = "2024-09-01"
PRICE_END_EXCLUSIVE = "2025-03-01"
HOLDING_INTERVALS = 20
RESULT_COLUMNS = ["平均全价涨跌幅", "上涨占比"]


def sql_list(values):
    return ",".join(str(int(v)) for v in sorted(set(values)))


def query(sql):
    result = query_to_dataframe(sql, max_rows=0, timeout=120)
    if result is None:
        raise ValueError("Database query returned None.")
    return result


def fetch_valuation_calendar():
    # Market-wide dates, independent of the selected bonds and future price gaps.
    raw = query(f"""
        SELECT DISTINCT DATE(ENDDATE) AS valuation_date
        FROM bond_cbvaluationall FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
        WHERE ENDDATE >= '{START_DATE}' AND ENDDATE < '{PRICE_END_EXCLUSIVE}'
        ORDER BY valuation_date
    """)
    if raw.empty:
        raise ValueError("Missing ChinaBond valuation calendar.")
    return pd.DatetimeIndex(pd.to_datetime(raw.valuation_date)).normalize().unique().sort_values()


def signal_dates(calendar):
    dates = pd.Series(calendar[(calendar >= START_DATE) & (calendar < SIGNAL_END_EXCLUSIVE)])
    signals = dates.groupby(dates.dt.to_period("M")).max()
    if list(signals.index.astype(str)) != ["2024-10", "2024-11", "2024-12"]:
        raise ValueError("The Q4 valuation calendar must cover all three months.")
    return signals.tolist()


def fetch_candidates(signals):
    template = Path(__file__).with_name("refer.sql").read_text(encoding="utf-8")
    frames = []
    for signal in signals:
        s = pd.Timestamp(signal).date().isoformat()
        frame = query(template.replace("{{ signal_date }}", s))
        if frame.empty:
            raise ValueError(f"No eligible candidate bonds at {s}.")
        frames.append(frame)
    candidates = pd.concat(frames, ignore_index=True)
    candidates["signal_date"] = pd.to_datetime(candidates.signal_date)
    candidates["MAINCODE"] = pd.to_numeric(candidates.MAINCODE).astype("int64")
    candidates["VPYIELD"] = pd.to_numeric(candidates.VPYIELD, errors="raise")
    if candidates.duplicated(["signal_date", "MAINCODE"]).any():
        raise ValueError("Duplicate economic bond in the candidate universe.")
    return candidates


def fetch_default_events(maincodes):
    # Missing public dates are retained for evidence checks, not silently
    # interpreted as either an actual default or a clean historical status.
    return query(f"""
        SELECT DISTINCT bc.MAINCODE, d.ID, d.INFOPUBLDATE, d.DEFAULTDATE,
               d.REVOKEDATE, d.DEFAULTPROG, d.DEFAULTTYPE
        FROM bond_code bc JOIN bond_default d ON bc.INNERCODE = d.INNERCODE
        WHERE bc.MAINCODE IN ({sql_list(maincodes)})
          AND (d.INFOPUBLDATE < '{SIGNAL_END_EXCLUSIVE}' OR d.INFOPUBLDATE IS NULL)
    """)


def exclude_known_defaults(candidates, events):
    if events.empty:
        return candidates.copy()
    events = events.copy()
    for col in ["INFOPUBLDATE", "DEFAULTDATE", "REVOKEDATE"]:
        events[col] = pd.to_datetime(events[col])
    # Actual principal/interest, substantive or technical default. A warning,
    # rating change or protection-clause record alone is not an actual default.
    actual = events.DEFAULTPROG.isin([201, 202, 203]) | events.DEFAULTTYPE.isin([2001, 2002])
    actual_events = events[actual]
    kept = []
    for s, group in candidates.groupby("signal_date", sort=True):
        d = actual_events[actual_events.MAINCODE.isin(group.MAINCODE)]
        cutoff = s + pd.Timedelta(days=1)
        potentially_prior = d.DEFAULTDATE.isna() | d.DEFAULTDATE.lt(cutoff)
        if (potentially_prior & d.INFOPUBLDATE.isna()).any():
            raise ValueError("Actual-default record lacks public-availability evidence.")
        d = d[d.INFOPUBLDATE.lt(cutoff)]
        if d.DEFAULTDATE.isna().any():
            raise ValueError("Published actual-default record lacks its event date.")
        d = d[d.DEFAULTDATE.lt(cutoff)]
        if d.REVOKEDATE.lt(cutoff).any():
            # REVOKEDATE alone does not date the public announcement of a cure.
            raise ValueError("Historical default revocation needs dated public evidence.")
        kept.append(group[~group.MAINCODE.isin(d.MAINCODE)])
    return pd.concat(kept, ignore_index=True)


def high_yield_pool(candidates):
    pools = []
    for s, group in candidates.groupby("signal_date", sort=True):
        if len(group) < 20:
            raise ValueError(f"Fewer than 20 candidates at {s.date()}; check source coverage.")
        pools.append(group.sort_values(["VPYIELD", "sort_code", "MAINCODE"],
                                       ascending=[False, True, True]).head(20))
    return pd.concat(pools, ignore_index=True)


def fetch_trade_calendar():
    raw = query(f"""
        SELECT DISTINCT DATE(TRADINGDATE) AS trade_date FROM qt_tradingdaynew
        WHERE SECUMARKET = 89 AND IFTRADINGDAY = 1
          AND TRADINGDATE >= '{TRADE_HISTORY_START}' AND TRADINGDATE < '{SIGNAL_END_EXCLUSIVE}'
        ORDER BY trade_date
    """)
    if raw.empty:
        raise ValueError("Missing interbank trading calendar.")
    raw["trade_date"] = pd.to_datetime(raw.trade_date)
    return raw


def fetch_trade_flags(maincodes):
    return query(f"""
        SELECT bc.MAINCODE, DATE(q.TRADINGDAY) AS trade_date, 1 AS traded_flag
        FROM bond_basicquote q FORCE INDEX (IX_BOND_BASICQUOTE)
        JOIN bond_code bc ON q.INNERCODE = bc.INNERCODE
        WHERE bc.MAINCODE IN ({sql_list(maincodes)})
          AND q.TRADINGDAY >= '{TRADE_HISTORY_START}'
          AND q.TRADINGDAY < '{SIGNAL_END_EXCLUSIVE}' AND q.IFTRADING = 1
        GROUP BY bc.MAINCODE, DATE(q.TRADINGDAY)
    """)


def select_portfolios(pool, trades, calendar):
    selected = []
    if not trades.empty:
        trades = trades.copy()
        trades["trade_date"] = pd.to_datetime(trades.trade_date)
    for s, group in pool.groupby("signal_date", sort=True):
        window = calendar.loc[calendar.trade_date.le(s), "trade_date"].tail(20)
        if len(window) != 20:
            raise ValueError(f"Incomplete 20-day interbank window at {s.date()}.")
        counts = (trades[trades.trade_date.isin(window)].groupby("MAINCODE").trade_date.nunique()
                  if not trades.empty else pd.Series(dtype=int))
        group = group.copy()
        group["trade_days_20"] = group.MAINCODE.map(counts).fillna(0).astype(int)
        chosen = group.sort_values(["trade_days_20", "sort_code", "MAINCODE"],
                                  ascending=[False, True, True]).head(10).copy()
        if len(chosen) != 10:
            raise ValueError(f"Incomplete ten-bond selection at {s.date()}.")
        selected.append(chosen)
    return pd.concat(selected, ignore_index=True)


def holding_dates(signals, calendar):
    rows = []
    for s in signals:
        entry = calendar.searchsorted(s, side="right")
        end = entry + HOLDING_INTERVALS
        if end >= len(calendar):
            raise ValueError("Insufficient market-wide calendar for the holding horizon.")
        rows.append({"signal_date": s, "entry_date": calendar[entry], "exit_date": calendar[end]})
    return pd.DataFrame(rows)


def fetch_prices(selected, periods):
    code_map = query(f"""
        SELECT DISTINCT MAINCODE, INNERCODE FROM bond_code
        WHERE MAINCODE IN ({sql_list(selected.MAINCODE)}) AND INNERCODE IS NOT NULL
    """)
    if code_map.empty or code_map.INNERCODE.duplicated().any():
        raise ValueError("Missing or ambiguous cross-market identity mapping.")
    days = sorted(set(periods.entry_date) | set(periods.exit_date))
    literals = ",".join("'" + day.date().isoformat() + "'" for day in days)
    raw = query(f"""
        SELECT INNERCODE, DATE(ENDDATE) AS price_date, VALUEFULLPRICE, INSERTTIME, ID
        FROM bond_cbvaluationall
        WHERE INNERCODE IN ({sql_list(code_map.INNERCODE)})
          AND ENDDATE IN ({literals}) AND CREDIBILITYCODE = 1 AND YIELDCODE = 2
    """)
    if raw.empty:
        raise ValueError("Missing endpoint valuations.")
    raw = raw.merge(code_map, on="INNERCODE", validate="many_to_one")
    raw["price_date"] = pd.to_datetime(raw.price_date)
    raw["INSERTTIME"] = pd.to_datetime(raw.INSERTTIME)
    raw["VALUEFULLPRICE"] = pd.to_numeric(raw.VALUEFULLPRICE, errors="raise")
    # These prices are ex-post labels. They never participate in candidate
    # eligibility, yield ranking or liquidity ranking.
    raw = raw.sort_values(["MAINCODE", "price_date", "INSERTTIME", "ID"],
                          ascending=[True, True, False, False], na_position="last")
    return raw.drop_duplicates(["MAINCODE", "price_date"])[["MAINCODE", "price_date", "VALUEFULLPRICE"]]


def build_holding_events(selected, prices, periods):
    prices = prices.set_index(["MAINCODE", "price_date"]).VALUEFULLPRICE
    rows = []
    for period in periods.itertuples():
        group = selected[selected.signal_date.eq(period.signal_date)]
        if len(group) != 10:
            raise ValueError("A Q4 portfolio is missing selected bonds.")
        returns = []
        for maincode in group.MAINCODE:
            entry = prices.get((maincode, period.entry_date), float("nan"))
            end = prices.get((maincode, period.exit_date), float("nan"))
            if pd.isna(entry) or pd.isna(end) or entry <= 0 or end <= 0:
                raise ValueError(f"Missing/invalid endpoint price for bond {maincode}; no reselection.")
            returns.append(float(end / entry - 1))
        rows.append({"signal_date": period.signal_date, "entry_date": period.entry_date,
                     "exit_date": period.exit_date, "selected_count": len(group),
                     "valid_bond_count": len(returns), "portfolio_return": sum(returns) / len(returns)})
    return pd.DataFrame(rows)


def summarize(events):
    if len(events) != 3 or events.portfolio_return.isna().any():
        raise ValueError("All three Q4 portfolio returns are required.")
    values = events.portfolio_return
    return pd.DataFrame([{
        RESULT_COLUMNS[0]: f"{values.mean() * 100:.2f}",
        RESULT_COLUMNS[1]: f"{values.gt(0).mean() * 100:.2f}",
    }], columns=RESULT_COLUMNS)


def build_backtest():
    calendar = fetch_valuation_calendar()
    signals = signal_dates(calendar)
    candidates = fetch_candidates(signals)
    candidates = exclude_known_defaults(candidates, fetch_default_events(candidates.MAINCODE))
    pool = high_yield_pool(candidates)
    trades = fetch_trade_flags(pool.MAINCODE)
    selected = select_portfolios(pool, trades, fetch_trade_calendar())
    periods = holding_dates(signals, calendar)
    prices = fetch_prices(selected, periods)
    events = build_holding_events(selected, prices, periods)
    return summarize(events), events, selected


def main():
    with shared_connection_session():
        result, _, _ = build_backtest()
    result.to_csv(Path(__file__).with_name("result.csv"), index=False, encoding="utf-8-sig")
    print(result.to_csv(index=False).strip())
    return result


if __name__ == "__main__":
    main()
