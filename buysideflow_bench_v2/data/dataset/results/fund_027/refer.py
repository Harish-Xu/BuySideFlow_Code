#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmark_paths import load_benchmark_env
from sweagent.text2sql.db_connector import query_to_dataframe
from sweagent.text2sql.provenance import require_db_snapshot_id

AS_OF_DATE = pd.Timestamp('2025-12-31')
CUTOFF = AS_OF_DATE + pd.Timedelta(days=1)
N_QUARTERS = 8

POSITION_SQL = """
SELECT a.ID, a.REPORTDATE, a.INFOPUBLDATE, a.INSERTTIME, a.UPDATETIME,
       a.RINOFSTOCK AS stock_position
FROM mf_assetallocationnew a
JOIN secumain s ON s.INNERCODE=a.INNERCODE
WHERE s.SECUCODE='163411' AND s.SECUCATEGORY=8
  AND a.REPORTDATE BETWEEN '2023-01-01' AND '2025-12-31'
  AND a.INFOPUBLDATE < '2026-01-01'
"""


def select_positions(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        raise ValueError('No disclosed allocation records for 163411.OF')
    rows = raw.copy()
    for col in ['REPORTDATE', 'INFOPUBLDATE', 'INSERTTIME', 'UPDATETIME']:
        rows[col] = pd.to_datetime(rows[col])
    rows['ID'] = pd.to_numeric(rows['ID'])
    rows['stock_position'] = pd.to_numeric(rows['stock_position'], errors='coerce')
    rows = rows[(rows.INFOPUBLDATE < CUTOFF) & (rows.REPORTDATE <= AS_OF_DATE)
                & rows.REPORTDATE.dt.is_quarter_end]
    # Select the record before checking its value: a latest NULL must remain NULL.
    rows = rows.sort_values(['INFOPUBLDATE', 'INSERTTIME', 'UPDATETIME', 'ID'],
                            na_position='first').drop_duplicates('REPORTDATE', keep='last')
    if rows.empty:
        raise ValueError('No disclosed quarter-end allocation records')
    rows['quarter'] = rows.REPORTDATE.dt.to_period('Q')
    quarters = pd.period_range(end=rows.quarter.max(), periods=N_QUARTERS + 1, freq='Q')
    rows = rows.set_index('quarter').reindex(quarters).rename_axis('quarter').reset_index()
    rows['report_date'] = rows.quarter.dt.end_time.dt.normalize()
    return rows


def load_positions() -> pd.DataFrame:
    return select_positions(query_to_dataframe(POSITION_SQL, max_rows=200, timeout=60))


def load_index_quarter_closes(quarters) -> pd.DataFrame:
    ends = sorted({q.end_time.normalize() for q in quarters if q.end_time.normalize() <= AS_OF_DATE})
    if not ends:
        return pd.DataFrame(columns=['quarter', 'trading_day', 'close_price'])
    endpoints = '\nUNION ALL\n'.join(f"SELECT DATE('{day:%Y-%m-%d}') AS quarter_end" for day in ends)
    start = ends[0].to_period('Q').start_time
    sql = f"""
    WITH quarter_ends AS ({endpoints}), calendar_ends AS (
        SELECT qe.quarter_end, MAX(c.TRADINGDATE) AS trading_day
        FROM quarter_ends qe LEFT JOIN qt_tradingdaynew c
          ON c.SECUMARKET=83 AND c.IFTRADINGDAY=1
         AND c.TRADINGDATE BETWEEN '{start:%Y-%m-%d}' AND qe.quarter_end
        GROUP BY qe.quarter_end
    ), ranked_quotes AS (
        SELECT e.quarter_end, e.trading_day, q.CLOSEPRICE AS close_price,
               ROW_NUMBER() OVER (PARTITION BY e.quarter_end ORDER BY q.ID DESC) AS rn
        FROM calendar_ends e
        JOIN secumain s ON s.SECUCODE='000300' AND s.SECUCATEGORY=4 AND s.SECUMARKET=83
        LEFT JOIN qt_indexquote q ON q.INNERCODE=s.INNERCODE AND q.TRADINGDAY=e.trading_day
    )
    SELECT quarter_end, trading_day, close_price FROM ranked_quotes WHERE rn=1
    ORDER BY quarter_end
    """
    rows = query_to_dataframe(sql, max_rows=20, timeout=60)
    rows['quarter'] = pd.to_datetime(rows.quarter_end).dt.to_period('Q')
    rows['close_price'] = pd.to_numeric(rows.close_price, errors='coerce')
    return rows


def calculate(positions: pd.DataFrame, closes: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = positions.copy()
    rows['position_change'] = rows.stock_position.diff()
    prices = closes.set_index('quarter').close_price
    rows['close_this'] = rows.quarter.map(prices)
    rows['close_next'] = (rows.quarter + 1).map(prices)
    finished = (rows.quarter + 1).dt.end_time.dt.normalize() <= AS_OF_DATE
    rows['index_return'] = (rows.close_next / rows.close_this - 1).where(
        finished & (rows.close_this > 0) & (rows.close_next > 0))
    rows = rows.iloc[1:].copy()
    valid = rows.position_change.notna() & rows.index_return.notna()
    same = (rows.position_change * rows.index_return) > 0
    detail = pd.DataFrame({
        '报告期': rows.report_date.dt.strftime('%Y-%m-%d'),
        '股票仓位': rows.stock_position * 100,
        '仓位变化': rows.position_change * 100,
        '下一季度沪深300收益': rows.index_return * 100,
        '是否同向': np.where(valid, np.where(same, '同向', '非同向'), '未计算'),
    }).reset_index(drop=True)
    count = int(valid.sum())
    summary = pd.DataFrame([{'有效季度数': count,
                             '同向占比': float(same[valid].mean() * 100) if count else np.nan}])
    return detail.round(2), summary.round(2)


def build_result() -> tuple[pd.DataFrame, pd.DataFrame]:
    positions = load_positions()
    quarters = list(positions.quarter.iloc[1:]) + [positions.quarter.iloc[-1] + 1]
    return calculate(positions, load_index_quarter_closes(quarters))


def main(output_dir: str | Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    load_benchmark_env()
    require_db_snapshot_id()
    outputs = build_result()
    destination = Path(output_dir) if output_dir is not None else Path(__file__).parent
    for name, result in zip(['result.csv', 'summary.csv'], outputs):
        result.to_csv(destination / name, index=False, encoding='utf-8-sig', float_format='%.2f')
        print(result.to_string(index=False))
    return outputs


if __name__ == '__main__':
    main()
