#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from sweagent.text2sql.db_connector import query_to_dataframe

ASOF_DATE = "2026-01-21"
FUND_CODE = "519736"
EXPECTED_TOP_N = 10
EXPECTED_REPORT_COUNT = 8

SQL = f"""
WITH params AS (
    SELECT
        DATE('{ASOF_DATE}') AS asof_date,
        DATE_ADD(DATE('{ASOF_DATE}'), INTERVAL 1 DAY) AS asof_next_day
),
target_fund AS (
    SELECT sm.INNERCODE
    FROM secumain sm
    WHERE sm.SECUCATEGORY = 8
      AND sm.SECUCODE = '{FUND_CODE}'
    ORDER BY sm.INNERCODE
    LIMIT 1
),
latest_8_reports AS (
    SELECT reportdate
    FROM (
        SELECT
            d.reportdate,
            ROW_NUMBER() OVER (ORDER BY d.reportdate DESC) AS rn
        FROM (
            SELECT DISTINCT k.REPORTDATE AS reportdate
            FROM mf_keystockportfolio k
            JOIN target_fund tf
              ON tf.INNERCODE = k.INNERCODE
            JOIN params p
              ON 1 = 1
            WHERE k.SERIALNUMBER BETWEEN 1 AND {EXPECTED_TOP_N}
              AND k.INFOPUBLDATE <= p.asof_date
              AND k.INSERTTIME < p.asof_next_day
              AND DATE_FORMAT(k.REPORTDATE, '%m-%d') IN ('03-31', '06-30', '09-30', '12-31')
        ) d
    ) ranked
    WHERE rn <= {EXPECTED_REPORT_COUNT}
),
target_top10 AS (
    SELECT
        x.REPORTDATE,
        x.STOCKINNERCODE
    FROM (
        SELECT
            k.REPORTDATE,
            k.STOCKINNERCODE,
            ROW_NUMBER() OVER (
                PARTITION BY k.REPORTDATE, k.STOCKINNERCODE
                ORDER BY k.INFOPUBLDATE DESC, k.INSERTTIME DESC, k.ID DESC
            ) AS dedup_rn
        FROM mf_keystockportfolio k
        JOIN target_fund tf
          ON tf.INNERCODE = k.INNERCODE
        JOIN latest_8_reports r
          ON r.reportdate = k.REPORTDATE
        JOIN params p
          ON 1 = 1
        WHERE k.SERIALNUMBER BETWEEN 1 AND {EXPECTED_TOP_N}
          AND k.INFOPUBLDATE <= p.asof_date
          AND k.INSERTTIME < p.asof_next_day
          AND k.STOCKINNERCODE IS NOT NULL
    ) x
    WHERE x.dedup_rn = 1
)
SELECT
    REPORTDATE,
    STOCKINNERCODE
FROM target_top10
ORDER BY REPORTDATE ASC, STOCKINNERCODE ASC
"""


def main() -> pd.DataFrame:
    df = query_to_dataframe(SQL, max_rows=5000, timeout=240)
    if df.empty:
        raise ValueError('未取到目标基金最近8个季报前十大重仓股。')

    df['REPORTDATE'] = pd.to_datetime(df['REPORTDATE'], errors='coerce')
    df['STOCKINNERCODE'] = pd.to_numeric(df['STOCKINNERCODE'], errors='coerce')
    df = df.dropna(subset=['REPORTDATE', 'STOCKINNERCODE']).copy()
    df['STOCKINNERCODE'] = df['STOCKINNERCODE'].astype(int)

    report_dates = sorted(df['REPORTDATE'].drop_duplicates().tolist())
    if len(report_dates) != EXPECTED_REPORT_COUNT:
        raise ValueError(f'报告期数量不是{EXPECTED_REPORT_COUNT}个，实际为{len(report_dates)}个。')

    stock_set_by_report = {d: set(df.loc[df['REPORTDATE'] == d, 'STOCKINNERCODE'].tolist()) for d in report_dates}
    bad_reports = {d.strftime('%Y-%m-%d'): len(stocks) for d, stocks in stock_set_by_report.items() if len(stocks) != EXPECTED_TOP_N}
    if bad_reports:
        raise ValueError(f'存在前十大重仓股数量不足或重复的报告期: {bad_reports}')

    rows = []
    for idx in range(1, len(report_dates)):
        prev_report = report_dates[idx - 1]
        curr_report = report_dates[idx]
        inter = len(stock_set_by_report[prev_report] & stock_set_by_report[curr_report])
        rows.append({
            '结果类型': '相邻区间',
            '相邻报告期': f"{prev_report:%Y-%m-%d} -> {curr_report:%Y-%m-%d}",
            '交集个数': inter,
            '单期留存率': round(inter / 10.0, 4),
            '平均留存率': '',
        })

    avg_retention = round(float(pd.Series([row['单期留存率'] for row in rows]).mean()), 4)
    rows.append({
        '结果类型': '平均留存率摘要',
        '相邻报告期': f"{report_dates[0]:%Y-%m-%d} -> {report_dates[-1]:%Y-%m-%d}",
        '交集个数': '',
        '单期留存率': '',
        '平均留存率': avg_retention,
    })
    out = pd.DataFrame(rows)
    out.to_csv(Path(__file__).with_name('result.csv'), index=False, encoding='utf-8-sig')
    print(out.to_string(index=False))
    return out


if __name__ == '__main__':
    main()
