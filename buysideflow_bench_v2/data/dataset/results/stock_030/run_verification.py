#!/usr/bin/env python3
"""
stock_030 数据验证脚本
执行verify_hypotheses.sql中的查询，验证Error-But-Pass的根本原因
"""

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sweagent.text2sql.db_connector import query_to_dataframe


def run_verification():
    """运行所有验证查询"""
    
    sql_path = Path(__file__).with_name("verify_hypotheses.sql")
    sql_content = sql_path.read_text(encoding="utf-8")
    
    # 分割多个查询（通过注释分隔符识别）
    queries = []
    current_query = []
    current_name = "未命名查询"
    
    for line in sql_content.split('\n'):
        if line.strip().startswith('-- ============================================================'):
            if current_query:
                queries.append((current_name, '\n'.join(current_query)))
                current_query = []
        elif line.strip().startswith('-- 验证'):
            current_name = line.strip()[3:].strip()
        elif line.strip() and not line.strip().startswith('--'):
            current_query.append(line)
    
    # 添加最后一个查询
    if current_query:
        queries.append((current_name, '\n'.join(current_query)))
    
    print("=" * 80)
    print("stock_030 Error-But-Pass 验证报告")
    print("=" * 80)
    print()
    
    # 执行每个查询
    for idx, (name, sql) in enumerate(queries, 1):
        print(f"\n{'=' * 80}")
        print(f"查询 {idx}/{len(queries)}: {name}")
        print(f"{'=' * 80}\n")
        
        # 分割单个SQL语句（可能有多个SELECT）
        statements = []
        current_stmt = []
        for line in sql.split('\n'):
            if line.strip():
                current_stmt.append(line)
                if line.strip().endswith(';'):
                    statements.append('\n'.join(current_stmt))
                    current_stmt = []
        
        if current_stmt:
            statements.append('\n'.join(current_stmt))
        
        # 执行每个语句
        for stmt_idx, stmt in enumerate(statements, 1):
            if not stmt.strip():
                continue
                
            try:
                print(f"执行语句 {stmt_idx}/{len(statements)}...")
                df = query_to_dataframe(stmt, max_rows=0, timeout=120)
                
                if df is None or df.empty:
                    print("  ⚠️  结果为空")
                else:
                    print(f"  ✅ 返回 {len(df)} 行数据\n")
                    print(df.to_string(index=False))
                    
                print()
                
            except Exception as e:
                print(f"  ❌ 查询失败: {e}")
                print()
    
    print("\n" + "=" * 80)
    print("验证完成")
    print("=" * 80)


def main():
    """主函数"""
    try:
        run_verification()
    except KeyboardInterrupt:
        print("\n\n用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 执行失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
