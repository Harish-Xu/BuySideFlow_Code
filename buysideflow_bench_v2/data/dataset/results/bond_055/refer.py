#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import matplotlib
    matplotlib.use("Agg")
except Exception:
    pass

from sweagent.text2sql.task_output_helper import install_print_capture, finalize_run

__print_buffer = install_print_capture()

import json
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1] if "__file__" in globals() else Path.cwd()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


START_DATE = '2023-01-01'
END_DATE = '2025-01-01'
QUOTE_END_DATE = '2025-03-31'
HOLDING_DAYS = 20


SQL = f"""
WITH price_daily AS (
    SELECT
        INNERCODE,
        DATE(VALIDDATE) AS signal_date,
        CONVERTPRICE,
        COALESCE(NULLIF(TRIM(CHANGEREASON), ''), '未披露') AS change_reason,
        ID,
        ROW_NUMBER() OVER (
            PARTITION BY INNERCODE, DATE(VALIDDATE)
            ORDER BY VALIDDATE DESC, ID DESC
        ) AS rn
    FROM bond_conbdconvertprice
    WHERE VALIDDATE < '{END_DATE}'
      AND INFOPUBLDATE < DATE_ADD(DATE(VALIDDATE), INTERVAL 1 DAY)
      AND CONVERTPRICE IS NOT NULL
),
price_seq AS (
    SELECT
        INNERCODE,
        signal_date,
        CONVERTPRICE,
        change_reason,
        ID,
        LAG(CONVERTPRICE) OVER (
            PARTITION BY INNERCODE
            ORDER BY signal_date, ID
        ) AS prev_convert_price
    FROM price_daily
    WHERE rn = 1
),
events AS (
    SELECT
        ROW_NUMBER() OVER (ORDER BY signal_date, INNERCODE) AS event_id,
        signal_date,
        INNERCODE AS asset_id,
        change_reason,
        prev_convert_price,
        CONVERTPRICE AS new_convert_price
    FROM price_seq
    WHERE signal_date >= '{START_DATE}'
      AND signal_date < '{END_DATE}'
      AND prev_convert_price IS NOT NULL
      AND CONVERTPRICE < prev_convert_price
),
trading_calendar AS (
    SELECT
        trade_date,
        LEAD(trade_date, {HOLDING_DAYS}) OVER (ORDER BY trade_date) AS exit_date
    FROM (
        SELECT DISTINCT DATE(TRADINGDATE) AS trade_date
        FROM qt_tradingdaynew
        WHERE SECUMARKET IN (83, 90)
          AND IFTRADINGDAY = 1
          AND TRADINGDATE >= '{START_DATE}'
          AND TRADINGDATE < '{QUOTE_END_DATE}'
    ) d
),
event_starts AS (
    SELECT e.event_id, MIN(t.trade_date) AS entry_date
    FROM events e
    JOIN trading_calendar t ON t.trade_date > e.signal_date
    GROUP BY e.event_id
),
event_dates AS (
    SELECT e.*, s.entry_date, t.exit_date
    FROM events e
    JOIN event_starts s ON s.event_id = e.event_id
    JOIN trading_calendar t ON t.trade_date = s.entry_date
    WHERE t.exit_date IS NOT NULL
),
event_quotes AS (
    SELECT
        e.event_id,
        e.signal_date,
        e.asset_id,
        e.change_reason,
        e.prev_convert_price,
        e.new_convert_price,
        e.entry_date,
        e.exit_date,
        DATE(q.TRADINGDAY) AS trade_date,
        q.CLOSEPRICE,
        ROW_NUMBER() OVER (
            PARTITION BY e.event_id, DATE(q.TRADINGDAY)
            ORDER BY q.ID DESC
        ) AS quote_version
    FROM event_dates e
    JOIN bond_conbdexchangequote q
      ON e.asset_id = q.INNERCODE
     AND q.TRADINGDAY IN (e.entry_date, e.exit_date)
     AND q.CLOSEPRICE IS NOT NULL
     AND q.CLOSEPRICE > 0
)
SELECT
    event_id,
    signal_date,
    asset_id,
    change_reason,
    prev_convert_price,
    new_convert_price,
    entry_date,
    MAX(CASE WHEN trade_date = entry_date THEN CLOSEPRICE END) AS entry_price,
    exit_date,
    MAX(CASE WHEN trade_date = exit_date THEN CLOSEPRICE END) AS exit_price
FROM event_quotes
WHERE quote_version = 1
GROUP BY
    event_id,
    signal_date,
    asset_id,
    change_reason,
    prev_convert_price,
    new_convert_price,
    entry_date,
    exit_date
HAVING entry_price IS NOT NULL
   AND exit_price IS NOT NULL
ORDER BY signal_date, asset_id;
"""


def empty_output() -> dict:
    return {
        'overall': {
            'sample_size': 0,
            'mean_return': None,
            'win_rate': None,
            'median_return': None,
        },
        'by_change_reason': [],
    }


def main() -> None:
    df = query_to_dataframe(SQL, max_rows=0, timeout=180)
    if df.empty:
        print(json.dumps(empty_output(), ensure_ascii=False, indent=2))
        return

    df = df.copy()
    df['entry_price'] = pd.to_numeric(df['entry_price'], errors='coerce')
    df['exit_price'] = pd.to_numeric(df['exit_price'], errors='coerce')
    df = df.dropna(subset=['entry_price', 'exit_price'])
    df = df[(df['entry_price'] > 0) & (df['exit_price'] > 0)].copy()

    if df.empty:
        print(json.dumps(empty_output(), ensure_ascii=False, indent=2))
        return

    df['holding_return'] = df['exit_price'] / df['entry_price'] - 1.0

    by_reason = (
        df.groupby('change_reason', dropna=False)['holding_return']
        .agg(
            sample_size='count',
            mean_return='mean',
            median_return='median',
            win_rate=lambda x: (x > 0).mean(),
        )
        .reset_index()
        .sort_values(['sample_size', 'change_reason'], ascending=[False, True])
    )

    output = {
        'overall': {
            'sample_size': int(len(df)),
            'mean_return': round(float(df['holding_return'].mean()), 6),
            'win_rate': round(float((df['holding_return'] > 0).mean()), 6),
            'median_return': round(float(df['holding_return'].median()), 6),
        },
        'by_change_reason': [
            {
                'change_reason': str(row['change_reason']),
                'sample_size': int(row['sample_size']),
                'mean_return': round(float(row['mean_return']), 6),
                'win_rate': round(float(row['win_rate']), 6),
                'median_return': round(float(row['median_return']), 6),
            }
            for _, row in by_reason.iterrows()
        ],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


__result_obj = None

if __name__ == "__main__":
    __caught_exc = None
    try:
        __result_obj = main()
    except BaseException as exc:  # noqa: BLE001
        __caught_exc = exc
    try:
        finalize_run(__result_obj, print_buffer=__print_buffer)
    except Exception:
        if __caught_exc is None:
            raise
    if __caught_exc is not None:
        raise __caught_exc
