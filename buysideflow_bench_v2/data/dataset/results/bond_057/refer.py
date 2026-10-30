#!/usr/bin/env python3
"""Quarterly pure-bond fund selection using only pre-open information."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from sweagent.text2sql.db_connector import query_to_dataframe

START_DATE = '2024-01-01'
END_DATE = '2025-01-01'
HISTORY_START_DATE = '2023-09-01'
LOOKBACK_DAYS = 60
PORTFOLIO_SIZE = 5
OUTPUT_COLUMNS = ['一季度收益', '二季度收益', '三季度收益', '四季度收益', '全年收益', '最大回撤']


def sql_date(value):
    return pd.Timestamp(value).date().isoformat()


def fetch_trade_calendar():
    sql = Path(__file__).with_name('refer.sql').read_text()
    sql = sql.replace('{{ HISTORY_START_DATE }}', HISTORY_START_DATE).replace('{{ END_DATE }}', END_DATE)
    data = query_to_dataframe(sql, max_rows=0, timeout=120)
    if data.empty:
        raise ValueError('Missing trading calendar')
    data['trade_date'] = pd.to_datetime(data['trade_date'])
    return data.drop_duplicates('trade_date').sort_values('trade_date').reset_index(drop=True)


def build_rebalance_quarters(calendar):
    calendar = calendar.sort_values('trade_date').reset_index(drop=True).copy()
    calendar['quarter'] = calendar.trade_date.dt.to_period('Q')
    inside = calendar[(calendar.trade_date >= START_DATE) & (calendar.trade_date < END_DATE)]
    quarters = []
    for quarter, rows in inside.groupby('quarter', sort=True):
        start = rows.index[0]
        history = calendar.iloc[max(0, start - LOOKBACK_DAYS - 1):start].trade_date.tolist()
        if len(history) != LOOKBACK_DAYS + 1:
            raise ValueError(f'{quarter}: need 61 prior market trading days')
        quarters.append({'quarter': str(quarter), 'signal_date': rows.trade_date.iloc[0],
                         'exit_date': rows.trade_date.iloc[-1], 'lookback_dates': history,
                         'holding_dates': rows.trade_date.tolist()})
    if len(quarters) != 4:
        raise ValueError('All four quarters are required')
    return quarters


def quarter_nav_sql(quarter):
    signal = sql_date(quarter['signal_date'])
    start = sql_date(quarter['lookback_dates'][0])
    end = sql_date(quarter['exit_date'])
    return f"""
    WITH shares AS (
        SELECT a.INNERCODE AS asset_id, a.SECURITYCODE AS fund_code
        FROM mf_fundarchives a
        WHERE a.MAINCODE = a.SECURITYCODE AND a.MAINCODE <> ''
          AND a.TYPE IN (2,3,4,7,8)
          AND a.ESTABLISHMENTDATE <= '{signal}'
          AND (a.EXPIREDATE IS NULL OR a.EXPIREDATE >= '{signal}')
          AND (a.LASTOPERATIONDATE IS NULL OR a.LASTOPERATIONDATE >= '{signal}')
    ), classifications AS (
        SELECT s.asset_id, s.fund_code, t.SECASSETCATCODE,
            ROW_NUMBER() OVER (
                PARTITION BY s.asset_id ORDER BY t.EFFECTIVEDATE DESC,t.INSERTTIME DESC,t.ID DESC
            ) AS rn
        FROM shares s JOIN mf_jyfundtype t ON t.INNERCODE=s.asset_id
        WHERE t.STANDARD=75 AND t.EFFECTIVEDATE <= '{signal}'
          AND (t.CANCELDATE IS NULL OR t.CANCELDATE > '{signal}')
          AND t.INSERTTIME < '{signal}'
    ), nav_versions AS (
        SELECT c.asset_id,c.fund_code,DATE(n.TRADINGDAY) AS trade_date,
            n.UNITNVRESTORED AS nav_restored,
            ROW_NUMBER() OVER (
                PARTITION BY c.asset_id,DATE(n.TRADINGDAY)
                ORDER BY n.INSERTTIME DESC,n.ID DESC
            ) AS rn
        FROM classifications c
        JOIN mf_fundnetvalueretrans n ON n.INNERCODE=c.asset_id
        WHERE c.rn=1 AND c.SECASSETCATCODE=1301
          AND n.TRADINGDAY >= '{start}' AND n.TRADINGDAY < DATE_ADD('{end}',INTERVAL 1 DAY)
          AND (n.TRADINGDAY >= '{signal}' OR n.INSERTTIME < '{signal}')
    )
    SELECT asset_id,fund_code,trade_date,nav_restored
    FROM nav_versions WHERE rn=1 AND nav_restored > 0
    ORDER BY fund_code,trade_date
    """


def fetch_quarter_nav(quarter):
    return query_to_dataframe(quarter_nav_sql(quarter), max_rows=0, timeout=600)


def select_quarter_portfolio(quarter, nav):
    if nav.empty:
        raise ValueError(f"{quarter['quarter']}: no NAV data")
    nav = nav.copy()
    nav['trade_date'] = pd.to_datetime(nav.trade_date)
    nav['nav_restored'] = pd.to_numeric(nav.nav_restored, errors='raise')
    nav['asset_id'] = pd.to_numeric(nav.asset_id, errors='raise').astype(int)
    if nav.duplicated(['asset_id', 'trade_date']).any():
        raise ValueError('NAV versions must be selected before portfolio construction')
    pivot = nav.pivot(index='trade_date', columns='asset_id', values='nav_restored').sort_index()
    history = pivot.reindex(pd.DatetimeIndex(quarter['lookback_dates']))
    history = history.loc[:, history.notna().all() & history.gt(0).all()]
    trailing = history.iloc[-1] / history.iloc[0] - 1
    codes = nav.drop_duplicates('asset_id').set_index('asset_id').fund_code
    candidates = pd.DataFrame({'total_return': trailing, 'fund_code': codes.reindex(trailing.index)})
    candidates = candidates[candidates.total_return > 0].sort_values(
        ['total_return', 'fund_code'], ascending=[False, True])
    if len(candidates) < PORTFOLIO_SIZE:
        raise ValueError('Fewer than five positive-return funds with 61 consecutive NAVs')
    anchor = int(candidates.index[0])
    returns = history[candidates.index].pct_change(fill_method=None).iloc[1:]
    correlations = returns.corrwith(returns[anchor]).drop(index=anchor).dropna()
    choices = pd.DataFrame({'correlation': correlations, 'fund_code': codes.reindex(correlations.index)})
    choices = choices[np.isfinite(choices.correlation)].sort_values(['correlation', 'fund_code'])
    if len(choices) < PORTFOLIO_SIZE - 1:
        raise ValueError('Fewer than four defined correlations to the leading fund')
    selected = [anchor] + choices.head(PORTFOLIO_SIZE - 1).index.tolist()

    # Holding-period availability must never change the pre-open selection.
    held = pivot.reindex(pd.DatetimeIndex(quarter['holding_dates']))[selected]
    if held.isna().any().any() or not held.gt(0).all().all():
        raise ValueError(f"{quarter['quarter']}: selected funds have missing holding NAVs")
    path = held.div(held.iloc[0]).mean(axis=1)
    return {'quarter': quarter['quarter'], 'selected_assets': selected,
            'selected_codes': codes.loc[selected].tolist(), 'anchor_asset': anchor,
            'positive_count': len(candidates), 'complete_count': history.shape[1],
            'quarter_return': float(path.iloc[-1] - 1), 'portfolio_path': path}


def build_result(quarters):
    if len(quarters) != 4:
        raise ValueError('All four quarterly portfolios are required')
    capital = 1.0
    strategy_values = [capital]
    for row in quarters:
        scaled = row['portfolio_path'] * capital
        strategy_values.extend(scaled.tolist())
        capital = float(scaled.iloc[-1])
    path = pd.Series(strategy_values)
    drawdown = (path / path.cummax() - 1).min()
    values = [row['quarter_return'] * 100 for row in quarters] + [(capital - 1) * 100, drawdown * 100]
    return pd.DataFrame([[f'{v:.2f}' for v in values]], columns=OUTPUT_COLUMNS)


def main():
    quarters = build_rebalance_quarters(fetch_trade_calendar())
    portfolios = [select_quarter_portfolio(q, fetch_quarter_nav(q)) for q in quarters]
    result = build_result(portfolios)
    result.to_csv(Path(__file__).with_name('result.csv'), index=False, encoding='utf-8-sig')
    print(result.to_string(index=False))
    return result


if __name__ == '__main__':
    main()
