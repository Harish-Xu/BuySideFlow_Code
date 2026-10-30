#!/usr/bin/env python3
"""
fund_109候选经理自动筛选工具

使用方法:
    python find_candidate.py --step 1  # 查找候选经理
    python find_candidate.py --step 2 --name "经理姓名"  # 检查边界样本
    python find_candidate.py --step 3 --innercodes "12345,67890"  # 验证净值
    python find_candidate.py --score  # 对所有候选评分排序
"""

import sys
import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from sweagent.text2sql.db_connector import query_to_dataframe
except ImportError:
    print("错误：无法导入db_connector，请确保在项目根目录运行")
    sys.exit(1)

import pandas as pd


class CandidateManagerFinder:
    """候选基金经理查找器"""
    
    ANCHOR_DATE = "2025-12-31"
    START_DATE = "2023-01-01"
    END_DATE = "2025-12-31"
    
    def __init__(self):
        self.candidates = []
        self.detailed_info = {}
    
    def step1_find_candidates(self):
        """Step 1: 查找候选经理"""
        print("=" * 60)
        print("Step 1: 查找管理多只主动权益产品的基金经理")
        print("=" * 60)
        
        sql = f"""
        SELECT 
            fm.NAME as manager_name,
            fm.PERSONALCODE as personal_code,
            COUNT(DISTINCT fm.INNERCODE) as total_funds,
            COUNT(DISTINCT CASE 
                WHEN fm.ACCESSIONDATE <= '{self.ANCHOR_DATE}'
                 AND (fm.DIMISSIONDATE IS NULL OR fm.DIMISSIONDATE > '{self.ANCHOR_DATE}')
                THEN fm.INNERCODE 
            END) as funds_at_anchor,
            COUNT(DISTINCT CASE 
                WHEN fm.DIMISSIONDATE BETWEEN '2025-01-01' AND '2026-06-30'
                THEN fm.INNERCODE 
            END) as recent_departures,
            MIN(fa.INVESTADVISORCODE) as primary_advisor_code
        FROM mf_fundmanagernew fm
        JOIN mf_fundarchives fa ON fm.INNERCODE = fa.INNERCODE
        JOIN mf_jyfundtype ft ON fm.INNERCODE = ft.INNERCODE
        WHERE fm.POSTNAME = 1
          AND fa.INVESTMENTTYPE = 16
          AND ft.STANDARD = 75
          AND ft.SECASSETCATCODE IN ('1101','1201','1202','1203')
          AND COALESCE(fa.IFFOF, 2) <> 1
          AND COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
          AND fa.ESTABLISHMENTDATE < '{self.START_DATE}'
          AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= '{self.ANCHOR_DATE}')
        GROUP BY fm.NAME, fm.PERSONALCODE
        HAVING total_funds >= 5
          AND funds_at_anchor >= 3
          AND recent_departures >= 2
        ORDER BY recent_departures DESC, funds_at_anchor DESC
        LIMIT 20
        """
        
        try:
            df = query_to_dataframe(sql, max_rows=0, timeout=120)
            if df.empty:
                print("⚠️  未找到符合条件的候选经理")
                return pd.DataFrame()
            
            print(f"✓ 找到 {len(df)} 位候选经理\n")
            print(df.to_string(index=False))
            print("\n推荐检查 Top 5 候选的边界样本分布")
            
            self.candidates = df.to_dict('records')
            return df
            
        except Exception as e:
            print(f"❌ 查询失败: {e}")
            return pd.DataFrame()
    
    def step2_check_boundary_samples(self, manager_name: str):
        """Step 2: 检查边界样本"""
        print("=" * 60)
        print(f"Step 2: 检查 '{manager_name}' 的边界样本")
        print("=" * 60)
        
        sql = f"""
        SELECT 
            fm.INNERCODE,
            fa.SECURITYCODE as fund_code,
            sm.SECUABBR as fund_name,
            fm.ACCESSIONDATE as join_date,
            fm.DIMISSIONDATE as leave_date,
            fm.INFOPUBLDATE as publish_date,
            fm.INCUMBENT as is_incumbent,
            CASE 
                WHEN fm.ACCESSIONDATE <= '{self.ANCHOR_DATE}'
                 AND fm.DIMISSIONDATE > '{self.ANCHOR_DATE}'
                 AND (fm.INFOPUBLDATE IS NULL OR fm.INFOPUBLDATE >= DATE_ADD('{self.ANCHOR_DATE}', INTERVAL 1 DAY))
                THEN 'Perfect'
                
                WHEN fm.ACCESSIONDATE <= '{self.ANCHOR_DATE}'
                 AND fm.DIMISSIONDATE > '{self.START_DATE}'
                 AND fm.DIMISSIONDATE <= '{self.ANCHOR_DATE}'
                 AND fm.INFOPUBLDATE >= DATE_ADD('{self.ANCHOR_DATE}', INTERVAL 1 DAY)
                THEN 'Good'
                
                WHEN fm.ACCESSIONDATE <= '{self.ANCHOR_DATE}'
                 AND (fm.DIMISSIONDATE IS NULL OR fm.DIMISSIONDATE > '{self.ANCHOR_DATE}')
                THEN 'Normal'
                
                WHEN fm.ACCESSIONDATE > '{self.ANCHOR_DATE}'
                THEN 'Useless'
                
                WHEN fm.DIMISSIONDATE < '{self.START_DATE}'
                THEN 'Irrelevant'
                
                ELSE 'Unknown'
            END as scenario,
            CASE 
                WHEN fm.ACCESSIONDATE <= '{self.ANCHOR_DATE}'
                 AND (fm.DIMISSIONDATE IS NULL OR fm.DIMISSIONDATE > '{self.ANCHOR_DATE}')
                THEN 'Y' ELSE 'N'
            END as in_correct_pool,
            CASE 
                WHEN fm.INCUMBENT = 1 THEN 'Y' ELSE 'N'
            END as in_wrong_pool
        FROM mf_fundmanagernew fm
        JOIN mf_fundarchives fa ON fm.INNERCODE = fa.INNERCODE
        JOIN secumain sm ON fm.INNERCODE = sm.INNERCODE
        JOIN mf_jyfundtype ft ON fm.INNERCODE = ft.INNERCODE
        WHERE fm.NAME = '{manager_name}'
          AND fm.POSTNAME = 1
          AND fa.INVESTMENTTYPE = 16
          AND ft.STANDARD = 75
          AND ft.SECASSETCATCODE IN ('1101','1201','1202','1203')
          AND COALESCE(fa.IFFOF, 2) <> 1
          AND COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
          AND fa.ESTABLISHMENTDATE < '{self.START_DATE}'
          AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= '{self.ANCHOR_DATE}')
        ORDER BY 
            FIELD(scenario, 'Perfect', 'Good', 'Normal', 'Useless', 'Irrelevant'),
            fm.DIMISSIONDATE DESC
        """
        
        try:
            df = query_to_dataframe(sql, max_rows=0, timeout=120)
            if df.empty:
                print(f"⚠️  '{manager_name}' 无符合条件的基金")
                return pd.DataFrame()
            
            # 统计场景分布
            scenario_counts = df['scenario'].value_counts()
            print("\n边界样本分布:")
            for scenario, count in scenario_counts.items():
                icon = {"Perfect": "⭐⭐⭐", "Good": "⭐⭐", "Normal": "✓", 
                       "Useless": "✗", "Irrelevant": "✗"}.get(scenario, "?")
                print(f"  {icon} {scenario}: {count}只")
            
            # 检查选池差异
            correct_only = df[(df['in_correct_pool'] == 'Y') & (df['in_wrong_pool'] == 'N')]
            if not correct_only.empty:
                print(f"\n✓ 发现 {len(correct_only)} 只基金会被错误SQL漏选:")
                print(correct_only[['fund_code', 'fund_name', 'scenario']].to_string(index=False))
            else:
                print("\n⚠️  两种SQL选出的基金池完全一致，无法暴露错误")
            
            print(f"\n完整列表 ({len(df)} 只基金):")
            print(df.to_string(index=False))
            
            self.detailed_info[manager_name] = df
            return df
            
        except Exception as e:
            print(f"❌ 查询失败: {e}")
            return pd.DataFrame()
    
    def step3_check_nav_coverage(self, innercode_list: str):
        """Step 3: 验证净值覆盖率"""
        print("=" * 60)
        print("Step 3: 验证净值数据完整性")
        print("=" * 60)
        
        sql = f"""
        SELECT 
            nv.INNERCODE,
            fa.SECURITYCODE,
            sm.SECUABBR,
            COUNT(*) as nav_count,
            MIN(nv.TRADINGDAY) as first_nav_date,
            MAX(nv.TRADINGDAY) as last_nav_date,
            DATEDIFF(MAX(nv.TRADINGDAY), MIN(nv.TRADINGDAY)) as span_days,
            ROUND(COUNT(*) * 100.0 / DATEDIFF(MAX(nv.TRADINGDAY), MIN(nv.TRADINGDAY)), 1) as coverage_pct
        FROM mf_fundnetvalueretrans nv
        JOIN mf_fundarchives fa ON nv.INNERCODE = fa.INNERCODE
        JOIN secumain sm ON nv.INNERCODE = sm.INNERCODE
        WHERE nv.INNERCODE IN ({innercode_list})
          AND nv.TRADINGDAY BETWEEN '{self.START_DATE}' AND '{self.END_DATE}'
          AND nv.UNITNVRESTORED IS NOT NULL
        GROUP BY nv.INNERCODE, fa.SECURITYCODE, sm.SECUABBR
        HAVING nav_count >= 500
        ORDER BY coverage_pct DESC
        """
        
        try:
            df = query_to_dataframe(sql, max_rows=0, timeout=120)
            if df.empty:
                print("⚠️  未找到符合要求的净值数据")
                return pd.DataFrame()
            
            print(f"✓ {len(df)} 只基金的净值覆盖率:\n")
            print(df.to_string(index=False))
            
            avg_coverage = df['coverage_pct'].mean()
            print(f"\n平均覆盖率: {avg_coverage:.1f}%")
            
            if avg_coverage >= 95:
                print("✓ 优秀（>95%）")
            elif avg_coverage >= 80:
                print("✓ 可用（80-95%）")
            else:
                print("⚠️  偏低（<80%），可能影响计算")
            
            return df
            
        except Exception as e:
            print(f"❌ 查询失败: {e}")
            return pd.DataFrame()
    
    def score_candidates(self):
        """对所有候选评分"""
        print("=" * 60)
        print("候选经理综合评分")
        print("=" * 60)
        
        if not self.detailed_info:
            print("⚠️  请先执行 step2 收集详细信息")
            return
        
        scores = []
        for manager_name, df in self.detailed_info.items():
            score = 0
            details = []
            
            # 统计场景
            scenario_counts = df['scenario'].value_counts().to_dict()
            perfect = scenario_counts.get('Perfect', 0)
            good = scenario_counts.get('Good', 0)
            normal = scenario_counts.get('Normal', 0)
            total_at_anchor = perfect + good + normal
            
            # 必需条件检查
            if total_at_anchor < 3:
                details.append(f"✗ 在任基金不足3只 ({total_at_anchor})")
                scores.append((manager_name, 0, "不满足必需条件", details))
                continue
            
            if perfect == 0 and good == 0:
                details.append("✗ 无边界样本")
                scores.append((manager_name, 0, "不满足必需条件", details))
                continue
            
            # 加分项
            if perfect >= 1:
                score += 50
                details.append(f"+50 有{perfect}只Perfect边界样本")
            
            if perfect + good >= 2:
                score += 30
                details.append(f"+30 有{perfect+good}只边界样本")
            
            if 3 <= total_at_anchor <= 5:
                score += 20
                details.append(f"+20 适中规模({total_at_anchor}只)")
            elif total_at_anchor > 8:
                score -= 10
                details.append(f"-10 规模过大({total_at_anchor}只)")
            else:
                score += 10
                details.append(f"+10 规模可接受({total_at_anchor}只)")
            
            # 检查是否能暴露错误
            correct_only = df[(df['in_correct_pool'] == 'Y') & (df['in_wrong_pool'] == 'N')]
            if len(correct_only) > 0:
                score += 40
                details.append(f"+40 能暴露{len(correct_only)}只基金的选池差异")
            else:
                score -= 50
                details.append("-50 无法暴露选池差异")
            
            # 评级
            if score >= 80:
                rating = "⭐⭐⭐ 优秀"
            elif score >= 60:
                rating = "⭐⭐ 可用"
            elif score >= 40:
                rating = "⭐ 勉强"
            else:
                rating = "✗ 不推荐"
            
            scores.append((manager_name, score, rating, details))
        
        # 排序输出
        scores.sort(key=lambda x: x[1], reverse=True)
        
        print("\n排名:")
        for i, (name, score, rating, details) in enumerate(scores, 1):
            print(f"\n{i}. {name} - {score}分 {rating}")
            for detail in details:
                print(f"   {detail}")
        
        if scores and scores[0][1] >= 60:
            print(f"\n推荐使用: {scores[0][0]} (得分: {scores[0][1]})")
        else:
            print("\n⚠️  无合适候选，建议放宽筛选条件")


def main():
    parser = argparse.ArgumentParser(description="fund_109候选经理筛选工具")
    parser.add_argument("--step", type=int, choices=[1, 2, 3], help="执行步骤")
    parser.add_argument("--name", type=str, help="经理姓名（Step 2）")
    parser.add_argument("--innercodes", type=str, help="INNERCODE列表（Step 3）")
    parser.add_argument("--score", action="store_true", help="评分所有候选")
    
    args = parser.parse_args()
    finder = CandidateManagerFinder()
    
    if args.step == 1:
        finder.step1_find_candidates()
    elif args.step == 2:
        if not args.name:
            print("错误：--step 2 需要提供 --name 参数")
            sys.exit(1)
        finder.step2_check_boundary_samples(args.name)
    elif args.step == 3:
        if not args.innercodes:
            print("错误：--step 3 需要提供 --innercodes 参数")
            sys.exit(1)
        finder.step3_check_nav_coverage(args.innercodes)
    elif args.score:
        print("请先依次执行 step 1 和 step 2，然后再评分")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
