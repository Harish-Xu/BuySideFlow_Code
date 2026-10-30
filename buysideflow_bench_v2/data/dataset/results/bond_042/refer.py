#!/usr/bin/env python3
"""Compare medians on the same bonds, alive and valued at both endpoints."""
from pathlib import Path
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from sweagent.text2sql.db_connector import query_to_dataframe

OUTPUT_COLUMNS = ['可比债券只数', '当期收益率中位数', '90日变化']


def build_result(paired: pd.DataFrame) -> pd.DataFrame:
    if paired.empty:
        return pd.DataFrame([[0, None, None]], columns=OUTPUT_COLUMNS)
    if paired.MAINCODE.duplicated().any():
        raise ValueError('Duplicate bond in comparable sample')
    values = paired[['current_yield', 'previous_yield']].apply(pd.to_numeric, errors='raise')
    if values.isna().any().any():
        raise ValueError('Both endpoints require a valid valuation')
    medians = values.median()
    return pd.DataFrame([[len(paired), round(medians.current_yield, 2),
                          round((medians.current_yield - medians.previous_yield) * 100, 2)]],
                        columns=OUTPUT_COLUMNS)


def main():
    directory = Path(__file__).resolve().parent
    paired = query_to_dataframe((directory / 'refer.sql').read_text(), max_rows=0, timeout=600)
    result = build_result(paired)
    result.to_csv(directory / 'result.csv', index=False, encoding='utf-8-sig', float_format='%.2f')
    print(result.to_string(index=False))
    return result


if __name__ == '__main__':
    main()
