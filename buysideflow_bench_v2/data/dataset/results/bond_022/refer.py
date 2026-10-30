#!/usr/bin/env python3
"""Daily yield/clean-price co-increases, separately for each market code."""
from pathlib import Path
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from sweagent.text2sql.db_connector import query_to_dataframe

OUTPUT_COLUMNS = ['估值日', '债券代码', '债券简称', '收益率变动', '净价变动']


def build_output(panel: pd.DataFrame) -> pd.DataFrame:
    if panel.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    df = panel.copy()
    df['val_date'] = pd.to_datetime(df['val_date'])
    df['prev_val_date'] = pd.to_datetime(df['prev_val_date'])
    for column in ['VPYIELD', 'VALUECLEANPRICE', 'prev_yield', 'prev_cleanprice']:
        df[column] = pd.to_numeric(df[column], errors='raise')
    df['yield_change'] = (df.VPYIELD - df.prev_yield) * 100
    df['price_change'] = df.VALUECLEANPRICE - df.prev_cleanprice
    gap = (df.val_date - df.prev_val_date).dt.days
    df = df[(gap >= 1) & (gap <= 5) & (df.yield_change > 0) & (df.price_change > 0)]
    df = df.sort_values(['val_date', 'yield_change', 'SECUCODE'], ascending=[True, False, True])
    return pd.DataFrame({
        '估值日': df.val_date.dt.strftime('%Y-%m-%d'),
        '债券代码': df.SECUCODE.astype(str), '债券简称': df.SECUABBR,
        '收益率变动': df.yield_change.map(lambda x: f'{x:.2f}'),
        '净价变动': df.price_change.map(lambda x: f'{x:.4f}'),
    }).reset_index(drop=True)


def main():
    directory = Path(__file__).resolve().parent
    panel = query_to_dataframe((directory / 'refer.sql').read_text(), max_rows=0, timeout=900)
    if panel is None:
        raise ValueError('SQL returned None')
    result = build_output(panel)
    for name in ['result.csv', 'result_detail.csv']:
        result.to_csv(directory / name, index=False, encoding='utf-8-sig')
    print(result.to_string(index=False))
    return result


if __name__ == '__main__':
    main()
