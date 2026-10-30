#!/usr/bin/env python3
"""Same-agency first-public AAA and current valid AA+ bond screen."""
from pathlib import Path
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from sweagent.text2sql.db_connector import query_to_dataframe

RESULT_COLUMNS = ['债券代码', '债券简称', '评级机构', '估值收益率']


def build_result(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    return pd.DataFrame({
        '债券代码': raw.bond_code.astype(str), '债券简称': raw.bond_abbr,
        '评级机构': raw.rating_agency,
        '估值收益率': pd.to_numeric(raw.valuation_yield_pct, errors='raise').map(lambda x: f'{x:.2f}'),
    })[RESULT_COLUMNS]


def main():
    directory = Path(__file__).resolve().parent
    raw = query_to_dataframe((directory / 'refer.sql').read_text(), max_rows=0, timeout=600)
    if raw is None:
        raise ValueError('SQL returned None')
    result = build_result(raw)
    result.to_csv(directory / 'result.csv', index=False, encoding='utf-8-sig')
    print(result.to_string(index=False))
    return result


if __name__ == '__main__':
    main()
