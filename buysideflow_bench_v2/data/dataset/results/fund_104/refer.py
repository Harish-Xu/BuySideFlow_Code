#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe

START_DATE = '2024-01-01'
AS_OF_DATE = '2025-07-13'
VISIBLE_CUTOFF = '2025-07-14'
OUTPUT_COLUMNS = ['基金代码', '基金简称', '年化收益率', '最大回撤']

FUND_POOL_SQL = f"""
WITH type_asof AS (
    SELECT t.INNERCODE, t.SECASSETCATCODE,
           ROW_NUMBER() OVER (
               PARTITION BY t.INNERCODE
               ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
           ) AS rn
    FROM mf_jyfundtype t
    WHERE t.STANDARD = 75 AND t.EFFECTIVEDATE <= '{AS_OF_DATE}'
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > '{AS_OF_DATE}')
      AND t.INSERTTIME < '{VISIBLE_CUTOFF}'
)
SELECT fa.INNERCODE, sm.SECUCODE, sm.SECUABBR
FROM mf_fundarchives fa
JOIN secumain sm ON sm.INNERCODE = fa.INNERCODE AND sm.SECUCATEGORY = 8
JOIN type_asof t ON t.INNERCODE = fa.INNERCODE AND t.rn = 1
WHERE t.SECASSETCATCODE = 1301
  AND fa.TYPE IN (2, 3, 4, 7, 8)
  AND fa.MAINCODE = fa.SECURITYCODE
  AND fa.ESTABLISHMENTDATE < '{START_DATE}'
  AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > '{AS_OF_DATE}')
"""

CALENDAR_SQL = f"""
SELECT DISTINCT TRADINGDATE FROM qt_tradingdaynew
WHERE SECUMARKET = 83 AND IFTRADINGDAY = 1
  AND TRADINGDATE >= '{START_DATE}' AND TRADINGDATE < '{AS_OF_DATE}'
ORDER BY TRADINGDATE
"""


def fetch_nav(innercodes, start, end, *, endpoints_only=False):
    """Restrict to the relevant funds/dates before deduplication and transfer."""
    codes = ','.join(str(int(code)) for code in innercodes)
    if not codes:
        return pd.DataFrame(columns=['INNERCODE', 'nav_day', 'nav'])
    if endpoints_only:
        date_filter = f"n.TRADINGDAY IN ('{start:%Y-%m-%d}', '{end:%Y-%m-%d}')"
    else:
        end_exclusive = end + pd.Timedelta(days=1)
        date_filter = (f"n.TRADINGDAY >= '{start:%Y-%m-%d}' "
                       f"AND n.TRADINGDAY < '{end_exclusive:%Y-%m-%d}'")
    sql = f"""
    WITH ranked AS (
        SELECT n.INNERCODE, DATE(n.TRADINGDAY) AS nav_day,
               n.UNITNVRESTORED AS nav,
               ROW_NUMBER() OVER (
                   PARTITION BY n.INNERCODE, DATE(n.TRADINGDAY)
                   ORDER BY n.INSERTTIME DESC, n.ID DESC
               ) AS rn
        FROM mf_fundnetvalueretrans n
        WHERE n.INNERCODE IN ({codes}) AND {date_filter}
          AND n.INSERTTIME < '{VISIBLE_CUTOFF}'
    )
    SELECT INNERCODE, nav_day, nav FROM ranked
    WHERE rn = 1 AND nav > 0 ORDER BY INNERCODE, nav_day
    """
    return query_to_dataframe(sql, max_rows=0, timeout=180)


def select_return_candidates(pool, endpoints, start, end, return_intervals):
    if return_intervals < 1:
        raise ValueError('Market interval must contain at least one return day.')
    if endpoints.empty:
        return pool.iloc[:0].assign(annual_return=pd.Series(dtype=float))
    endpoints = endpoints.copy()
    endpoints['nav_day'] = pd.to_datetime(endpoints['nav_day'])
    endpoints['nav'] = pd.to_numeric(endpoints['nav'], errors='coerce')
    pivot = endpoints.pivot(index='INNERCODE', columns='nav_day', values='nav')
    pivot = pivot.reindex(columns=[start, end]).dropna()
    pivot = pivot[(pivot[start] > 0) & (pivot[end] > 0)].copy()
    # Annualize over market return intervals, not NAV observation count.
    growth = pivot[end] / pivot[start]
    pivot['annual_return'] = (growth ** (250.0 / return_intervals) - 1) * 100
    # Compare growth factors directly: subtracting 1 can turn exactly 5% into 5% + epsilon.
    eligible = pivot.loc[growth > 1.05 ** (return_intervals / 250.0), ['annual_return']].reset_index()
    print(f'端点完整={len(pivot)}; 年化收益率>5%={len(eligible)}')
    return pool.merge(eligible, on='INNERCODE', how='inner')


def build_result(candidates, nav):
    if candidates.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    if nav.empty:
        raise ValueError('Missing NAV paths for return-qualified funds.')
    nav = nav.copy()
    nav['nav_day'] = pd.to_datetime(nav['nav_day'])
    nav['nav'] = pd.to_numeric(nav['nav'], errors='coerce')
    if nav['nav'].isna().any() or (nav['nav'] <= 0).any():
        raise ValueError('NAV paths must contain positive valid values.')
    nav = nav.sort_values(['INNERCODE', 'nav_day'])
    nav['relative_nav'] = nav['nav'] / nav.groupby('INNERCODE')['nav'].cummax()
    min_ratio = nav.groupby('INNERCODE')['relative_nav'].min().rename('min_ratio')
    out = candidates.merge(min_ratio, left_on='INNERCODE', right_index=True, how='left')
    if out['min_ratio'].isna().any():
        raise ValueError('Missing drawdown data for return-qualified funds.')
    out = out[out['min_ratio'] > 0.995].copy()
    # Screen the positive magnitude above; report the signed minimum drawdown.
    out['max_drawdown'] = (out['min_ratio'] - 1) * 100
    # Both threshold checks and ordering precede display rounding.
    out = out.sort_values(['annual_return', 'SECUCODE'], ascending=[False, True])
    out = out.rename(columns={'SECUCODE': '基金代码', 'SECUABBR': '基金简称',
                              'annual_return': '年化收益率', 'max_drawdown': '最大回撤'})
    out = out[OUTPUT_COLUMNS].reset_index(drop=True)
    out[['年化收益率', '最大回撤']] = out[['年化收益率', '最大回撤']].round(2)
    return out


def main():
    pool = query_to_dataframe(FUND_POOL_SQL, max_rows=0, timeout=120)
    if pool.empty:
        raise ValueError('Initial fund pool is empty; check source coverage.')
    calendar = query_to_dataframe(CALENDAR_SQL, max_rows=0, timeout=60)
    days = pd.DatetimeIndex(pd.to_datetime(calendar['TRADINGDATE'])).drop_duplicates().sort_values()
    if len(days) < 2:
        raise ValueError('Insufficient market calendar coverage.')
    start, end = days[0], days[-1]
    print(f'初始基金池={len(pool)}; 端点={start:%Y-%m-%d}至{end:%Y-%m-%d}; 收益间隔={len(days)-1}')
    endpoints = fetch_nav(pool['INNERCODE'], start, end, endpoints_only=True)
    candidates = select_return_candidates(pool, endpoints, start, end, len(days) - 1)
    nav = fetch_nav(candidates['INNERCODE'], start, end)
    out = build_result(candidates, nav)
    out.to_csv(Path(__file__).with_name('result.csv'), index=False,
               encoding='utf-8-sig', float_format='%.2f')
    print(out.to_string(index=False))
    return out


if __name__ == '__main__':
    main()
