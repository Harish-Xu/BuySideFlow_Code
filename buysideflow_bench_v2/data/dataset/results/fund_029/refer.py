#!/usr/bin/env python3
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
    df = query_to_dataframe(sql, max_rows=0, timeout=600)
    if df is None or df.empty:
        raise ValueError('Q029查询结果为空')
    out = df.rename(columns={
        'report_year':'年度',
        'year_end_nav_date':'年度最后净值日',
        'annual_return_pct':'年度收益率',
        'peer_rank_over_count':'同类排名/样本数',
        'max_drawdown_pct':'年度最大回撤',
    })[['年度','年度最后净值日','年度收益率','同类排名/样本数','年度最大回撤']]
    out.to_csv(Path(__file__).with_name('result.csv'), index=False, encoding='utf-8-sig')
    print(out.to_string(index=False))
    return out

if __name__ == '__main__':
    main()
