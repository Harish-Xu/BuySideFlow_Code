#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path
from string import Template

import pandas as pd

INJECTED_SQL = globals().get('sql_code', '')
if not INJECTED_SQL:
    REPO_ROOT = Path(__file__).resolve().parents[4]
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe

AS_OF_DATE = '2025-07-31'
EXPECTED_COLUMNS = ['股票代码', '股票简称', '当前PS(TTM)', '历史分位', '最新季度营收同比增速']
SQL_BLOCK_RE = re.compile(r'^-- name:\s*(\w+)\s*$\n(.*?)(?=^-- name:|\Z)', re.M | re.S)


def load_sql_blocks():
    text = INJECTED_SQL or Path(__file__).with_name('refer.sql').read_text()
    return dict(SQL_BLOCK_RE.findall(text))


def run_block(blocks, name, **params):
    sql = Template(blocks[name]).substitute(as_of_date=AS_OF_DATE, **params)
    result = query_to_dataframe(sql, max_rows=0, timeout=300)
    if result is None:
        raise ValueError(f'{name} returned None')
    return result


def query_chunks(blocks, name, values, parameter, **params):
    values = sorted({int(value) for value in values})
    parts = [run_block(blocks, name, **{parameter: ','.join(map(str, values[i:i + 300]))}, **params)
             for i in range(0, len(values), 300)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def finish(candidates, current, history):
    if current.empty:
        raise ValueError('PSTTM coverage missing for all financially eligible securities')
    panel = candidates.merge(current, on='INNERCODE', how='left', validate='one_to_one')
    missing = panel.PSTTM.isna()
    if missing.any():
        codes = ','.join(panel.loc[missing, 'SECUCODE'].astype(str).str.zfill(6))
        raise ValueError(f'PSTTM coverage missing for {int(missing.sum())} financially eligible securities: {codes}')
    panel = panel[panel.PSTTM > 0].copy()
    if panel.empty:
        return pd.DataFrame(columns=EXPECTED_COLUMNS)
    if history.empty:
        raise ValueError('Positive PSTTM history is unavailable')
    valid = history[history.PSTTM.notna() & (history.PSTTM > 0)]
    samples = valid.merge(panel[['INNERCODE', 'PSTTM']], on='INNERCODE', suffixes=('_history', '_current'))
    samples['below_current'] = samples.PSTTM_history <= samples.PSTTM_current
    percentiles = samples.groupby('INNERCODE').below_current.mean().rename('percentile')
    panel = panel.join(percentiles, on='INNERCODE')
    if panel.percentile.isna().any():
        raise ValueError('Positive PSTTM history is missing for a current positive valuation')
    panel = panel[panel.percentile <= 0.15].copy()
    panel['SECUCODE'] = panel.SECUCODE.astype(str).str.zfill(6)
    panel = panel.sort_values(['percentile', 'SECUCODE'])
    return pd.DataFrame({
        '股票代码': panel.SECUCODE, '股票简称': panel.stock_name,
        '当前PS(TTM)': panel.PSTTM.round(2), '历史分位': panel.percentile.round(4),
        '最新季度营收同比增速': panel.latest_growth.round(2),
    }).reset_index(drop=True)


def calculate():
    blocks = load_sql_blocks()
    universe = run_block(blocks, 'stock_universe')
    if universe.empty:
        raise ValueError('Historical listing universe is unavailable')
    calendar = run_block(blocks, 'previous_trading_day')
    if len(calendar) != 1 or calendar.td.isna().any():
        raise ValueError('The previous Shenzhen trading day is required')
    trading_day = str(calendar.td.iloc[0])[:10]
    financial = query_chunks(blocks, 'financial_candidates', universe.COMPANYCODE, 'company_values')
    if financial.empty:
        return pd.DataFrame(columns=EXPECTED_COLUMNS)
    candidates = universe.merge(financial, on='COMPANYCODE', validate='one_to_one')
    current = query_chunks(blocks, 'current_valuation', candidates.INNERCODE, 'innercode_values', trading_day=trading_day)
    history = query_chunks(blocks, 'valuation_history', candidates.INNERCODE, 'innercode_values', trading_day=trading_day)
    return finish(candidates, current, history)


def main(*, write_output=True):
    result = calculate()
    if write_output:
        result.to_csv(Path(__file__).with_name('result.csv'), index=False, encoding='utf-8-sig')
    print(result.to_string(index=False) if not result.empty else '<empty result>')
    return result


if __name__ == '__main__':
    result = main(write_output=not bool(INJECTED_SQL))
