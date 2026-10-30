#!/usr/bin/env python3
"""Fixed-endpoint three-year balanced-fund comparison, with no gap returns."""
from pathlib import Path
import sys
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from sweagent.text2sql.db_connector import query_to_dataframe

START = pd.Timestamp('2022-08-08')
END = pd.Timestamp('2025-08-08')
NAV_SQL = """
WITH type_asof AS (
    SELECT t.INNERCODE, t.SECASSETCATCODE,
           ROW_NUMBER() OVER (PARTITION BY t.INNERCODE
               ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC) AS rn
    FROM mf_jyfundtype t
    WHERE t.STANDARD = 75 AND t.EFFECTIVEDATE <= '2025-08-11'
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > '2025-08-11')
      AND t.INSERTTIME < '2025-08-11'
), fund_pool AS (
    SELECT fa.INNERCODE, fa.SECURITYCODE AS fund_code, sm.SECUABBR AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain sm ON sm.INNERCODE = fa.INNERCODE AND sm.SECUCATEGORY = 8
    JOIN type_asof t ON t.INNERCODE = fa.INNERCODE AND t.rn = 1
    WHERE t.SECASSETCATCODE = 1203
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= '2022-08-08'
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > '2025-08-11')
), nav_ranked AS (
    SELECT f.*, DATE(n.TRADINGDAY) AS trading_day, n.UNITNVRESTORED AS adj_nav,
           ROW_NUMBER() OVER (PARTITION BY f.INNERCODE, DATE(n.TRADINGDAY)
               ORDER BY n.INSERTTIME DESC, n.ID DESC) AS rn
    FROM fund_pool f
    JOIN mf_fundnetvalueretrans n ON n.INNERCODE = f.INNERCODE
    WHERE n.TRADINGDAY >= '2022-08-08' AND n.TRADINGDAY < '2025-08-09'
      AND n.INSERTTIME < '2025-08-11'
)
SELECT INNERCODE, fund_code, fund_name, trading_day, adj_nav
FROM nav_ranked WHERE rn = 1 ORDER BY fund_code, trading_day
"""
CALENDAR_SQL = """
SELECT DISTINCT DATE(TRADINGDATE) AS trading_day
FROM qt_tradingdaynew
WHERE SECUMARKET = 83 AND IFTRADINGDAY = 1
  AND TRADINGDATE >= '2022-08-08' AND TRADINGDATE < '2025-08-09'
ORDER BY trading_day
"""

def calculate(nav: pd.DataFrame, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    calendar = pd.DatetimeIndex(calendar).sort_values().unique()
    if len(calendar) < 2 or calendar[0] != START or calendar[-1] != END:
        raise ValueError('The exact required endpoints and market calendar are missing.')
    n_return_intervals = len(calendar) - 1  # Exclude START; include END.
    nav = nav.copy()
    nav['trading_day'] = pd.to_datetime(nav['trading_day'])
    nav['adj_nav'] = pd.to_numeric(nav['adj_nav'], errors='coerce')
    nav.loc[~np.isfinite(nav['adj_nav']) | (nav['adj_nav'] <= 0), 'adj_nav'] = np.nan
    rows = []
    for (_, code, name), frame in nav.groupby(['INNERCODE', 'fund_code', 'fund_name']):
        path = frame.set_index('trading_day')['adj_nav'].reindex(calendar)
        if pd.isna(path.iloc[0]) or pd.isna(path.iloc[-1]):
            continue
        # Reindex first: a missing market date invalidates both adjacent returns.
        returns = path.pct_change(fill_method=None).dropna()
        if returns.empty:
            continue
        annual_return = (path.iloc[-1] / path.iloc[0]) ** (250 / n_return_intervals) - 1
        annual_vol = returns.std(ddof=0) * np.sqrt(252)
        rows.append({'基金代码': code, '基金简称': name,
                     '近3年年化收益率': annual_return, '近3年年化波动率': annual_vol})
    peer = pd.DataFrame(rows)
    if peer.empty:
        raise ValueError('No funds have both calculable metrics.')
    avg_return = peer['近3年年化收益率'].mean()
    avg_vol = peer['近3年年化波动率'].mean()
    out = peer[(peer['近3年年化收益率'] > avg_return)
               & (peer['近3年年化波动率'] < avg_vol)].copy()
    out = out.sort_values(['近3年年化收益率', '基金代码'], ascending=[False, True])
    # Round only for display, after full-precision means, filtering and sorting.
    for col in ['近3年年化收益率', '近3年年化波动率']:
        out[col] = (out[col] * 100).round(2)
    print(f'比较组={len(peer)}; 年化收益均值={avg_return:.10f}; 波动均值={avg_vol:.10f}')
    return out.reset_index(drop=True)

def main():
    nav = query_to_dataframe(NAV_SQL, max_rows=0, timeout=300)
    days = query_to_dataframe(CALENDAR_SQL, max_rows=0, timeout=120)
    if nav.empty or days.empty:
        raise ValueError('Missing fund NAV data or market calendar.')
    result = calculate(nav, pd.DatetimeIndex(days['trading_day']))
    result.to_csv(Path(__file__).with_name('result.csv'), index=False, encoding='utf-8-sig')
    print(result.to_string(index=False))
    return result

if __name__ == '__main__':
    main()
