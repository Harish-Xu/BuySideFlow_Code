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

FUND_CODE = '005505'
ANCHOR_DAY = '2026-02-27'
CUTOFF = pd.Timestamp('2026-02-28')
HOLDING_REPORT = START_DAY = '2025-06-30'
END_DAY = '2025-12-31'


def latest(rows: pd.DataFrame, keys: list[str], order: list[str]) -> pd.DataFrame:
    return rows.sort_values(order, na_position='first').drop_duplicates(keys, keep='last')


def select_holdings(raw: pd.DataFrame) -> pd.DataFrame:
    rows = raw.copy()
    for col in ['REPORTDATE', 'INFOPUBLDATE', 'INSERTTIME']:
        rows[col] = pd.to_datetime(rows[col])
    for col in ['ID', 'INNERCODE', 'INVESTTYPE', 'SERIALNUMBER', 'STOCKINNERCODE', 'RATIOINNV']:
        rows[col] = pd.to_numeric(rows[col], errors='coerce')
    # Comprehensive investment already includes its domestic/active/index subgroups.
    rows = rows[(rows.REPORTDATE == pd.Timestamp(HOLDING_REPORT))
                & (rows.INVESTTYPE == 1) & (rows.INFOPUBLDATE < CUTOFF)]
    rows = latest(rows, ['INNERCODE', 'REPORTDATE', 'INVESTTYPE', 'SERIALNUMBER'],
                  ['INFOPUBLDATE', 'INSERTTIME', 'ID'])
    if rows.empty:
        raise ValueError('No disclosed 2025 H1 stock holdings for 005505.OF')
    return rows.sort_values('SERIALNUMBER').reset_index(drop=True)


def fetch_holdings() -> pd.DataFrame:
    sql = f"""
    SELECT h.ID, h.INNERCODE, h.REPORTDATE, h.INFOPUBLDATE, h.INSERTTIME,
           h.INVESTTYPE, h.SERIALNUMBER, h.STOCKINNERCODE, h.RATIOINNV,
           s.COMPANYCODE, s.SECUCODE
    FROM mf_stockportfoliodetail h
    JOIN secumain f ON f.INNERCODE=h.INNERCODE AND f.SECUCODE='{FUND_CODE}' AND f.SECUCATEGORY=8
    LEFT JOIN secumain s ON s.INNERCODE=h.STOCKINNERCODE
    WHERE h.REPORTDATE='{HOLDING_REPORT}' AND h.INVESTTYPE=1
      AND h.INFOPUBLDATE < '2026-02-28'
    """
    return select_holdings(query_to_dataframe(sql, max_rows=2000, timeout=60))


def select_industries(raw: pd.DataFrame) -> pd.DataFrame:
    rows = raw.copy()
    for col in ['effective_date', 'INFOPUBLDATE', 'CANCELDATE', 'INSERTTIME']:
        rows[col] = pd.to_datetime(rows[col])
    for col in ['ID', 'COMPANYCODE', 'source_rank']:
        rows[col] = pd.to_numeric(rows[col])
    day = pd.Timestamp(HOLDING_REPORT)
    rows = rows[(rows.effective_date <= day) & (rows.INFOPUBLDATE < day + pd.Timedelta(days=1))
                & (rows.CANCELDATE.isna() | (rows.CANCELDATE > day))]
    rows = latest(rows, ['COMPANYCODE'], ['source_rank', 'effective_date', 'INFOPUBLDATE', 'INSERTTIME', 'ID'])
    return rows[['COMPANYCODE', 'SECONDINDUSTRYNAME']]


def fetch_industries(companycodes) -> pd.DataFrame:
    codes = ','.join(str(int(v)) for v in sorted(set(companycodes.dropna())))
    if not codes:
        return pd.DataFrame(columns=['COMPANYCODE', 'SECONDINDUSTRYNAME'])
    sql = f"""
    SELECT COMPANYCODE, SECONDINDUSTRYNAME, INFOPUBLDATE AS effective_date,
           INFOPUBLDATE, CANCELDATE, INSERTTIME, ID, 2 AS source_rank
    FROM lc_exgindustry WHERE COMPANYCODE IN ({codes}) AND STANDARD=38
      AND INFOPUBLDATE < '2025-07-01'
    UNION ALL
    SELECT COMPANYCODE, SECONDINDUSTRYNAME, EFFECTIVEDATE AS effective_date,
           INFOPUBLDATE, CANCELDATE, INSERTTIME, ID, 1 AS source_rank
    FROM lc_stibexgindustry WHERE COMPANYCODE IN ({codes}) AND STANDARD=38
      AND EFFECTIVEDATE <= '{HOLDING_REPORT}' AND INFOPUBLDATE < '2025-07-01'
    """
    raw = query_to_dataframe(sql, max_rows=10000, timeout=60)
    return select_industries(raw) if not raw.empty else pd.DataFrame(columns=['COMPANYCODE', 'SECONDINDUSTRYNAME'])


def select_endpoints(raw: pd.DataFrame, value: str, *, nav: bool = False) -> pd.DataFrame:
    columns = ['INNERCODE', 'start_value', 'end_value']
    if raw.empty:
        return pd.DataFrame(columns=columns)
    rows = raw.copy()
    for col in ['TRADINGDAY', 'UPDATETIME'] + (['INSERTTIME'] if nav else []):
        rows[col] = pd.to_datetime(rows[col])
    for col in ['ID', 'INNERCODE', value]:
        rows[col] = pd.to_numeric(rows[col], errors='coerce')
    rows = rows[rows.TRADINGDAY.isin(pd.to_datetime([START_DAY, END_DAY]))]
    if nav:
        rows = rows[rows.INSERTTIME < CUTOFF]
    order = (['INSERTTIME'] if nav else []) + ['UPDATETIME', 'ID']
    rows = latest(rows, ['INNERCODE', 'TRADINGDAY'], order)
    # Latest missing/invalid values do not resurrect earlier versions or nearby dates.
    rows[value] = rows[value].where(rows[value] > 0)
    values = rows.pivot(index='INNERCODE', columns='TRADINGDAY', values=value)
    values = values.reindex(columns=pd.to_datetime([START_DAY, END_DAY]))
    values.columns = ['start_value', 'end_value']
    return values.reset_index()[columns]


def fetch_stock_endpoints(stock_innercodes) -> pd.DataFrame:
    ids = ','.join(str(int(v)) for v in sorted(set(stock_innercodes.dropna())))
    if not ids:
        return pd.DataFrame(columns=['INNERCODE', 'start_value', 'end_value'])
    raw = query_to_dataframe(f"""
        SELECT ID, INNERCODE, TRADINGDAY, BACKWARDPRICE, UPDATETIME
        FROM qt_performancedata WHERE INNERCODE IN ({ids})
          AND TRADINGDAY IN ('{START_DAY}', '{END_DAY}')
    """, max_rows=10000, timeout=60)
    return select_endpoints(raw, 'BACKWARDPRICE')


def fetch_fund_endpoints(innercode: int) -> pd.DataFrame:
    raw = query_to_dataframe(f"""
        SELECT ID, INNERCODE, TRADINGDAY, UNITNVRESTORED, INSERTTIME, UPDATETIME
        FROM mf_fundnetvalueretrans WHERE INNERCODE={int(innercode)}
          AND TRADINGDAY IN ('{START_DAY}', '{END_DAY}') AND INSERTTIME < '2026-02-28'
    """, max_rows=100, timeout=60)
    return select_endpoints(raw, 'UNITNVRESTORED', nav=True)


def calculate(holdings: pd.DataFrame, industries: pd.DataFrame, prices: pd.DataFrame,
              nav: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = holdings.merge(industries, on='COMPANYCODE', how='left', validate='many_to_one')
    rows = rows.merge(prices.rename(columns={'INNERCODE': 'STOCKINNERCODE'}),
                      on='STOCKINNERCODE', how='left', validate='many_to_one')
    rows['industry'] = rows.SECONDINDUSTRYNAME.fillna('未分类')
    rows['stock_return'] = rows.end_value / rows.start_value - 1
    rows['contribution'] = rows.RATIOINNV * rows.stock_return * 100
    rows['missing_weight'] = rows.RATIOINNV.where(rows.stock_return.isna(), 0.0)
    groups = rows.groupby('industry', sort=False)
    output = groups.agg(**{
        '中报持仓权重': ('RATIOINNV', lambda v: v.sum(skipna=False) * 100),
        '缺价持仓权重': ('missing_weight', lambda v: v.sum(skipna=False) * 100),
        '可计算持仓的静态贡献': ('contribution', lambda v: v.sum(min_count=1)),
    }).rename_axis('行业').reset_index()
    output = output.sort_values(['可计算持仓的静态贡献', '行业'], ascending=[False, True],
                                na_position='last').reset_index(drop=True)
    fund_return = np.nan if nav.empty else (nav.end_value.iloc[0] / nav.start_value.iloc[0] - 1) * 100
    summary = pd.DataFrame([{'基金同期收益率': fund_return,
                             '可计算持仓的静态贡献合计': rows.contribution.sum(min_count=1)}])
    return output.round(4), summary.round(4)


def build_result() -> tuple[pd.DataFrame, pd.DataFrame]:
    holdings = fetch_holdings()
    return calculate(holdings, fetch_industries(holdings.COMPANYCODE),
                     fetch_stock_endpoints(holdings.STOCKINNERCODE),
                     fetch_fund_endpoints(int(holdings.INNERCODE.iloc[0])))


def main(output_dir: str | Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    load_benchmark_env()
    require_db_snapshot_id()
    outputs = build_result()
    destination = Path(output_dir) if output_dir is not None else Path(__file__).parent
    for filename, result in zip(['industry_contributions.csv', 'return_summary.csv'], outputs):
        result.to_csv(destination / filename, index=False, encoding='utf-8-sig', float_format='%.4f')
        print(result.to_string(index=False))
    return outputs


if __name__ == '__main__':
    main()
