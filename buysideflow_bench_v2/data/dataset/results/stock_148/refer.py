#!/usr/bin/env python3
"""Export the point-in-time electronics ROE ranking."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


def main() -> pd.DataFrame:
    sql = Path(__file__).with_name('refer.sql').read_text(encoding='utf-8')
    result = query_to_dataframe(sql, max_rows=0, timeout=600)
    if result is None or len(result) != 10:
        raise ValueError('Expected ten companies with disclosed 2025 Q3 TTM ROE')
    result.to_csv(Path(__file__).with_name('result.csv'), index=False, encoding='utf-8-sig')
    print(result.to_string(index=False))
    return result


if __name__ == '__main__':
    main()
