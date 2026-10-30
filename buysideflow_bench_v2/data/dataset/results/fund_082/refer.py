#!/usr/bin/env python3
from __future__ import annotations
import json
import sys
import zlib
from pathlib import Path
import pandas as pd
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from sweagent.text2sql.db_connector import query_to_dataframe
SQL_QUERY = """
WITH anchor_date AS (
    SELECT DATE('2024-12-31') AS anchor_day,
           TIMESTAMP('2025-01-01 00:00:00') AS visible_cutoff
), type_asof AS (
    SELECT t.INNERCODE, t.SECASSETCATCODE,
           ROW_NUMBER() OVER (PARTITION BY t.INNERCODE ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC) AS rn
    FROM mf_jyfundtype t JOIN anchor_date ad ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= ad.anchor_day AND (t.CANCELDATE IS NULL OR t.CANCELDATE > ad.anchor_day)
      AND t.INSERTTIME < ad.visible_cutoff
), fund_pool AS (
    SELECT f.INNERCODE, sm.SECUCODE AS fund_code, sm.SECUABBR
    FROM mf_fundarchives f
    JOIN type_asof ta ON ta.INNERCODE = f.INNERCODE AND ta.rn = 1 AND ta.SECASSETCATCODE = 1101
    JOIN secumain sm ON sm.INNERCODE = f.INNERCODE
    JOIN anchor_date ad ON 1 = 1
    WHERE f.MAINCODE = f.SECURITYCODE
      AND COALESCE(f.IFFOF, 2) <> 1
      AND f.ESTABLISHMENTDATE <= ad.anchor_day
      AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE > ad.anchor_day)
), trade_days_500 AS (
    SELECT trading_day FROM (
        SELECT td.TRADINGDATE AS trading_day
        FROM qt_tradingdaynew td JOIN anchor_date ad ON 1 = 1
        WHERE td.SECUMARKET IN (83, 90) AND td.IFTRADINGDAY = 1 AND td.TRADINGDATE <= ad.anchor_day
        GROUP BY td.TRADINGDATE ORDER BY td.TRADINGDATE DESC LIMIT 500
    ) d
), nav_dedup AS (
    SELECT fp.fund_code, fp.SECUABBR AS fund_name, n.TRADINGDAY, n.UNITNVRESTORED,
           ROW_NUMBER() OVER (PARTITION BY fp.fund_code, n.TRADINGDAY ORDER BY n.TRADINGDAY DESC, n.INSERTTIME DESC, n.ID DESC) AS rn
    FROM fund_pool fp JOIN mf_fundnetvalueretrans n ON n.INNERCODE = fp.INNERCODE
    JOIN trade_days_500 d ON d.trading_day = n.TRADINGDAY
    JOIN anchor_date ad ON 1 = 1
    WHERE n.UNITNVRESTORED IS NOT NULL
      AND n.INSERTTIME < ad.visible_cutoff
)
SELECT
    nd.fund_code,
    nd.fund_name,
    COMPRESS(
        JSON_OBJECTAGG(DATE_FORMAT(nd.TRADINGDAY, '%Y%m%d'), nd.UNITNVRESTORED)
    ) AS nav_history
FROM nav_dedup nd
WHERE nd.rn = 1
GROUP BY nd.fund_code, nd.fund_name
HAVING COUNT(*) = 500
ORDER BY nd.fund_code
"""

def calc_recovery_days(group: pd.DataFrame):
    series = group.sort_values('TRADINGDAY').reset_index(drop=True).copy()
    if len(series) != 500:
        return None
    series['UNITNVRESTORED'] = pd.to_numeric(series['UNITNVRESTORED'], errors='coerce')
    if series['UNITNVRESTORED'].isna().any():
        return None
    series['running_peak'] = series['UNITNVRESTORED'].cummax()
    series['peak_date'] = series['TRADINGDAY'].where(series['UNITNVRESTORED'].eq(series['running_peak'])).ffill()
    series['drawdown'] = series['UNITNVRESTORED'] / series['running_peak'] - 1.0
    trough_pos = int(series['drawdown'].values.argmin())
    max_drawdown = float(series.loc[trough_pos, 'drawdown'])
    if pd.isna(max_drawdown) or max_drawdown >= 0:
        return None
    peak_date = pd.Timestamp(series.loc[trough_pos, 'peak_date'])
    trough_date = pd.Timestamp(series.loc[trough_pos, 'TRADINGDAY'])
    peak_before_trough = float(series.loc[trough_pos, 'running_peak'])
    recovered = series.iloc[trough_pos + 1:]
    recovered = recovered[recovered['UNITNVRESTORED'] >= peak_before_trough]
    if recovered.empty:
        return None
    recovery_row = recovered.iloc[0]
    recovery_days = int(recovery_row.name - trough_pos)
    return {
        '基金代码': str(series.loc[0, 'fund_code']).zfill(6),
        '基金简称': str(series.loc[0, 'fund_name']),
        '最大回撤': max_drawdown * 100,
        '前高日': peak_date.strftime('%Y-%m-%d'),
        '谷底日': trough_date.strftime('%Y-%m-%d'),
        '修复日': pd.Timestamp(recovery_row['TRADINGDAY']).strftime('%Y-%m-%d'),
        '修复交易日数': recovery_days,
    }

def decode_nav_history(value) -> dict[str, float]:
    compressed = bytes(value)
    if len(compressed) < 5:
        raise ValueError('净值历史压缩数据无效')
    expected_size = int.from_bytes(compressed[:4], byteorder='little')
    payload = zlib.decompress(compressed[4:])
    if len(payload) != expected_size:
        raise ValueError('净值历史解压长度不一致')
    return json.loads(payload.decode('utf-8'))

def build_review_summary(df: pd.DataFrame) -> list[str]:
    lines = []
    for idx, row in df.head(3).reset_index(drop=True).iterrows():
        lines.append(f"{row['基金代码']} {row['基金简称']}：前高日 {row['前高日']}，谷底日 {row['谷底日']}，修复日 {row['修复日']}，最大回撤 {row['最大回撤']:.2f}%，谷底后 {int(row['修复交易日数'])} 个交易日修复。")
    return lines

def main() -> pd.DataFrame:
    aggregated = query_to_dataframe(SQL_QUERY, max_rows=0, timeout=600)
    if aggregated.empty:
        raise ValueError('SQL返回空数据，请检查筛选条件')
    rows = []
    for fund in aggregated.itertuples(index=False):
        history = decode_nav_history(fund.nav_history)
        group = pd.DataFrame({
            'fund_code': fund.fund_code,
            'fund_name': fund.fund_name,
            'TRADINGDAY': pd.to_datetime(list(history), format='%Y%m%d'),
            'UNITNVRESTORED': list(history.values()),
        })
        item = calc_recovery_days(group)
        if item is not None:
            rows.append(item)
    result = pd.DataFrame(rows)
    if result.empty:
        raise ValueError('未找到满足条件的基金')
    result = result.sort_values(['修复交易日数', '最大回撤', '基金代码'], ascending=[True, False, True]).head(10).reset_index(drop=True)
    result['最大回撤'] = result['最大回撤'].round(2)
    result = result[['基金代码','基金简称','前高日','谷底日','修复日','最大回撤','修复交易日数']]
    result['复核说明'] = ''
    for idx, line in enumerate(build_review_summary(result)):
        result.loc[idx, '复核说明'] = line
    out = result[['基金代码','基金简称','前高日','谷底日','修复日','最大回撤','修复交易日数']]
    out.to_csv(Path(__file__).with_name('result.csv'), index=False, encoding='utf-8-sig', float_format='%.2f')
    print(out.to_string(index=False))
    return out

if __name__ == '__main__':
    main()
