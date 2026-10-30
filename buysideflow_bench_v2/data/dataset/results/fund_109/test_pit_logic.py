#!/usr/bin/env python3
"""
fund_109 PIT逻辑验证脚本

用于验证正确的PIT逻辑和错误的INCUMBENT逻辑的差异
"""

import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from sweagent.text2sql.db_connector import query_to_dataframe


def test_correct_pit_logic():
    """测试正确的PIT逻辑"""
    sql = """
WITH params AS (
    SELECT
        DATE('2023-01-01') AS start_day,
        DATE('2025-12-31') AS anchor_day,
        DATE('2026-01-01') AS next_day
),
manager_asof AS (
    SELECT DISTINCT
        m.INNERCODE
    FROM mf_fundmanagernew m
    JOIN params p ON 1 = 1
    WHERE m.PERSONALCODE = 101001672
      AND m.POSTNAME = 1
      AND m.ACCESSIONDATE <= p.anchor_day
      AND (
          m.DIMISSIONDATE IS NULL 
          OR m.DIMISSIONDATE > p.anchor_day
          OR (m.DIMISSIONDATE <= p.anchor_day AND m.INFOPUBLDATE >= p.next_day)
      )
),
type_asof AS (
    SELECT
        t.INNERCODE,
        t.SECASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS rn
    FROM mf_jyfundtype t
    JOIN params p ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= p.anchor_day
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.anchor_day)
      AND t.INSERTTIME < p.next_day
),
fund_pool AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        sm.SECUABBR AS fund_name
    FROM manager_asof ma
    JOIN mf_fundarchives fa ON fa.INNERCODE = ma.INNERCODE
    JOIN secumain sm ON sm.INNERCODE = fa.INNERCODE AND sm.SECUCATEGORY = 8
    JOIN type_asof ta ON ta.INNERCODE = fa.INNERCODE AND ta.rn = 1
    JOIN params p ON 1 = 1
    WHERE fa.INVESTADVISORCODE = 41511
      AND ta.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
      AND fa.INVESTMENTTYPE = 16
      AND fa.TYPE IN (2, 3, 7, 8)
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE < p.start_day
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.anchor_day)
)
SELECT
    fp.fund_code,
    fp.fund_name
FROM fund_pool fp
ORDER BY fp.fund_code
"""
    
    print("=" * 80)
    print("测试1: 正确的PIT逻辑")
    print("=" * 80)
    print("逻辑: 使用 DIMISSIONDATE > anchor_day OR (DIMISSIONDATE <= anchor_day AND INFOPUBLDATE >= next_day)")
    print()
    
    df = query_to_dataframe(sql, max_rows=0, timeout=60)
    print(df.to_string(index=False))
    print()
    print(f"✅ 结果: {len(df)} 只基金")
    print()
    
    return df


def test_wrong_incumbent_logic():
    """测试错误的INCUMBENT逻辑"""
    sql = """
WITH params AS (
    SELECT
        DATE('2023-01-01') AS start_day,
        DATE('2025-12-31') AS anchor_day,
        DATE('2026-01-01') AS next_day
),
manager_asof_wrong AS (
    SELECT DISTINCT
        m.INNERCODE
    FROM mf_fundmanagernew m
    WHERE m.PERSONALCODE = 101001672
      AND m.POSTNAME = 1
      AND m.INCUMBENT = 1
),
type_asof AS (
    SELECT
        t.INNERCODE,
        t.SECASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS rn
    FROM mf_jyfundtype t
    JOIN params p ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= p.anchor_day
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.anchor_day)
      AND t.INSERTTIME < p.next_day
),
fund_pool AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        sm.SECUABBR AS fund_name
    FROM manager_asof_wrong ma
    JOIN mf_fundarchives fa ON fa.INNERCODE = ma.INNERCODE
    JOIN secumain sm ON sm.INNERCODE = fa.INNERCODE AND sm.SECUCATEGORY = 8
    JOIN type_asof ta ON ta.INNERCODE = fa.INNERCODE AND ta.rn = 1
    JOIN params p ON 1 = 1
    WHERE fa.INVESTADVISORCODE = 41511
      AND ta.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
      AND fa.INVESTMENTTYPE = 16
      AND fa.TYPE IN (2, 3, 7, 8)
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE < p.start_day
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.anchor_day)
)
SELECT
    fp.fund_code,
    fp.fund_name
FROM fund_pool fp
ORDER BY fp.fund_code
"""
    
    print("=" * 80)
    print("测试2: 错误的INCUMBENT逻辑")
    print("=" * 80)
    print("逻辑: 使用 INCUMBENT = 1")
    print()
    
    df = query_to_dataframe(sql, max_rows=0, timeout=60)
    if len(df) > 0:
        print(df.to_string(index=False))
    else:
        print("(空结果)")
    print()
    print(f"❌ 结果: {len(df)} 只基金")
    print()
    
    return df


def verify_manager_data():
    """验证孙轶佳的基础数据"""
    sql = """
SELECT
    m.INNERCODE,
    sm.SECUABBR AS fund_name,
    m.ACCESSIONDATE,
    m.DIMISSIONDATE,
    m.INFOPUBLDATE,
    m.INCUMBENT,
    CASE
        WHEN m.DIMISSIONDATE > '2025-12-31' THEN '✓ 离任在锚点后'
        WHEN m.DIMISSIONDATE <= '2025-12-31' AND m.INFOPUBLDATE >= '2026-01-01' THEN '✓ 离任在锚点前但公告在锚点后'
        ELSE '✗ 其他情况'
    END AS pit_scenario
FROM mf_fundmanagernew m
JOIN mf_fundarchives fa ON fa.INNERCODE = m.INNERCODE
JOIN secumain sm ON sm.INNERCODE = fa.INNERCODE AND sm.SECUCATEGORY = 8
WHERE m.PERSONALCODE = 101001672
  AND m.POSTNAME = 1
  AND fa.INVESTADVISORCODE = 41511
  AND fa.INVESTMENTTYPE = 16
  AND COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
ORDER BY m.DIMISSIONDATE DESC, fa.SECURITYCODE
"""
    
    print("=" * 80)
    print("验证: 孙轶佳的任职数据")
    print("=" * 80)
    print()
    
    df = query_to_dataframe(sql, max_rows=0, timeout=60)
    print(df.to_string(index=False))
    print()
    print(f"总共 {len(df)} 条任职记录")
    print()


def main():
    """主函数"""
    print("\nfund_109 PIT逻辑验证\n")
    
    # 验证基础数据
    verify_manager_data()
    
    # 测试正确逻辑
    correct_df = test_correct_pit_logic()
    
    # 测试错误逻辑
    wrong_df = test_wrong_incumbent_logic()
    
    # 总结
    print("=" * 80)
    print("总结")
    print("=" * 80)
    print(f"正确的PIT逻辑: {len(correct_df)} 只基金")
    print(f"错误的INCUMBENT逻辑: {len(wrong_df)} 只基金")
    print()
    
    if len(correct_df) > 0 and len(wrong_df) == 0:
        print("✅ 验证通过！题目能够有效区分正确和错误的逻辑。")
        return 0
    else:
        print("❌ 验证失败！题目无法区分正确和错误的逻辑。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
