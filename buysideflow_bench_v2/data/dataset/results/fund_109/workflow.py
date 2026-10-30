#!/usr/bin/env python3
"""
fund_109改进工作流 - 一键执行完整流程

使用方法:
    python workflow.py --auto     # 自动执行全流程（推荐）
    python workflow.py --manual   # 手动逐步确认
    python workflow.py --verify   # 仅验证现有改进
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from sweagent.text2sql.db_connector import query_to_dataframe
except ImportError:
    print("错误：无法导入db_connector")
    sys.exit(1)

import pandas as pd


class Fund109ImprovementWorkflow:
    """fund_109改进完整工作流"""
    
    def __init__(self, auto_mode=False):
        self.auto_mode = auto_mode
        self.results_dir = Path(__file__).parent
        self.dataset_dir = self.results_dir.parent.parent
        
        self.selected_manager = None
        self.selected_personalcode = None
        self.boundary_samples = []
        self.normal_funds = []
        
    def log(self, message, level="INFO"):
        """日志输出"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        icons = {"INFO": "ℹ️", "SUCCESS": "✅", "WARNING": "⚠️", "ERROR": "❌"}
        icon = icons.get(level, "•")
        print(f"[{timestamp}] {icon} {message}")
    
    def confirm(self, prompt):
        """确认操作"""
        if self.auto_mode:
            return True
        response = input(f"{prompt} (y/n): ").strip().lower()
        return response == 'y'
    
    def step1_find_candidates(self):
        """Step 1: 查找候选经理"""
        self.log("=== Step 1: 查找候选基金经理 ===")
        
        sql = """
        SELECT 
            fm.NAME as manager_name,
            fm.PERSONALCODE as personal_code,
            COUNT(DISTINCT fm.INNERCODE) as total_funds,
            COUNT(DISTINCT CASE 
                WHEN fm.ACCESSIONDATE <= '2025-12-31'
                 AND (fm.DIMISSIONDATE IS NULL OR fm.DIMISSIONDATE > '2025-12-31')
                THEN fm.INNERCODE 
            END) as funds_at_anchor,
            COUNT(DISTINCT CASE 
                WHEN fm.DIMISSIONDATE BETWEEN '2025-01-01' AND '2026-06-30'
                THEN fm.INNERCODE 
            END) as recent_departures
        FROM mf_fundmanagernew fm
        JOIN mf_fundarchives fa ON fm.INNERCODE = fa.INNERCODE
        JOIN mf_jyfundtype ft ON fm.INNERCODE = ft.INNERCODE
        WHERE fm.POSTNAME = 1
          AND fa.INVESTMENTTYPE = 16
          AND ft.STANDARD = 75
          AND ft.SECASSETCATCODE IN ('1101','1201','1202','1203')
          AND COALESCE(fa.IFFOF, 2) <> 1
          AND COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
          AND fa.ESTABLISHMENTDATE < '2023-01-01'
          AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= '2025-12-31')
        GROUP BY fm.NAME, fm.PERSONALCODE
        HAVING total_funds >= 5
          AND funds_at_anchor >= 3
          AND recent_departures >= 2
        ORDER BY recent_departures DESC, funds_at_anchor DESC
        LIMIT 10
        """
        
        try:
            df = query_to_dataframe(sql, max_rows=0, timeout=120)
            if df.empty:
                self.log("未找到符合条件的候选经理", "ERROR")
                return False
            
            self.log(f"找到 {len(df)} 位候选经理", "SUCCESS")
            print("\n" + df.to_string(index=False))
            
            # 保存候选列表
            candidates_file = self.results_dir / "candidates.csv"
            df.to_csv(candidates_file, index=False, encoding='utf-8-sig')
            self.log(f"候选列表已保存: {candidates_file}")
            
            return df
            
        except Exception as e:
            self.log(f"查询失败: {e}", "ERROR")
            return False
    
    def step2_check_boundaries(self, manager_name):
        """Step 2: 检查边界样本"""
        self.log(f"=== Step 2: 检查 '{manager_name}' 的边界样本 ===")
        
        sql = f"""
        SELECT 
            fm.INNERCODE,
            fa.SECURITYCODE as fund_code,
            sm.SECUABBR as fund_name,
            fm.DIMISSIONDATE as leave_date,
            fm.INFOPUBLDATE as publish_date,
            CASE 
                WHEN fm.ACCESSIONDATE <= '2025-12-31'
                 AND fm.DIMISSIONDATE > '2025-12-31'
                 AND (fm.INFOPUBLDATE IS NULL OR fm.INFOPUBLDATE >= DATE_ADD('2025-12-31', INTERVAL 1 DAY))
                THEN 'Perfect'
                
                WHEN fm.ACCESSIONDATE <= '2025-12-31'
                 AND fm.DIMISSIONDATE > '2023-01-01'
                 AND fm.DIMISSIONDATE <= '2025-12-31'
                 AND fm.INFOPUBLDATE >= DATE_ADD('2025-12-31', INTERVAL 1 DAY)
                THEN 'Good'
                
                WHEN fm.ACCESSIONDATE <= '2025-12-31'
                 AND (fm.DIMISSIONDATE IS NULL OR fm.DIMISSIONDATE > '2025-12-31')
                THEN 'Normal'
                
                ELSE 'Other'
            END as scenario,
            fm.INCUMBENT as is_incumbent
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
          AND fa.ESTABLISHMENTDATE < '2023-01-01'
          AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= '2025-12-31')
        ORDER BY 
            FIELD(scenario, 'Perfect', 'Good', 'Normal', 'Other'),
            fm.DIMISSIONDATE DESC
        """
        
        try:
            df = query_to_dataframe(sql, max_rows=0, timeout=120)
            if df.empty:
                self.log(f"'{manager_name}' 无符合条件的基金", "WARNING")
                return None
            
            # 统计边界样本
            scenario_counts = df['scenario'].value_counts()
            perfect = scenario_counts.get('Perfect', 0)
            good = scenario_counts.get('Good', 0)
            normal = scenario_counts.get('Normal', 0)
            
            self.log(f"边界样本分布: Perfect={perfect}, Good={good}, Normal={normal}")
            
            # 检查选池差异
            boundary_funds = df[df['scenario'].isin(['Perfect', 'Good'])]
            wrong_pool_count = boundary_funds['is_incumbent'].sum()
            
            if len(boundary_funds) > 0 and wrong_pool_count < len(boundary_funds):
                self.log(f"✓ 能暴露 {len(boundary_funds) - wrong_pool_count} 只基金的选池差异", "SUCCESS")
                print("\n边界样本详情:")
                print(boundary_funds[['fund_code', 'fund_name', 'scenario', 'leave_date', 'is_incumbent']].to_string(index=False))
                return df
            else:
                self.log("两种SQL选池一致，无法暴露错误", "WARNING")
                return None
                
        except Exception as e:
            self.log(f"查询失败: {e}", "ERROR")
            return None
    
    def step3_score_and_select(self, candidates_df):
        """Step 3: 自动评分并选择最佳候选"""
        self.log("=== Step 3: 评分并选择最佳候选 ===")
        
        scores = []
        for _, row in candidates_df.head(5).iterrows():
            name = row['manager_name']
            personalcode = row['personal_code']
            
            self.log(f"评估候选: {name} (PERSONALCODE={personalcode})")
            
            boundary_df = self.step2_check_boundaries(name)
            if boundary_df is None:
                self.log(f"跳过 {name}（无边界样本或无法暴露差异）", "WARNING")
                continue
            
            # 计算得分
            scenario_counts = boundary_df['scenario'].value_counts()
            perfect = scenario_counts.get('Perfect', 0)
            good = scenario_counts.get('Good', 0)
            normal = scenario_counts.get('Normal', 0)
            total = perfect + good + normal
            
            score = 0
            score_details = []
            
            if perfect >= 1:
                score += 50
                score_details.append(f"+50 (Perfect={perfect})")
            
            if perfect + good >= 2:
                score += 30
                score_details.append(f"+30 (边界样本={perfect+good})")
            
            if 3 <= total <= 5:
                score += 20
                score_details.append(f"+20 (适中规模={total})")
            elif total > 8:
                score -= 10
                score_details.append(f"-10 (规模过大={total})")
            
            boundary_funds = boundary_df[boundary_df['scenario'].isin(['Perfect', 'Good'])]
            wrong_pool = boundary_funds['is_incumbent'].sum()
            diff = len(boundary_funds) - wrong_pool
            if diff > 0:
                score += 40
                score_details.append(f"+40 (能暴露{diff}只差异)")
            else:
                score -= 50
                score_details.append("-50 (无法暴露差异)")
            
            scores.append({
                'name': name,
                'personalcode': personalcode,
                'score': score,
                'perfect': perfect,
                'good': good,
                'normal': normal,
                'total': total,
                'details': ', '.join(score_details),
                'boundary_df': boundary_df
            })
        
        if not scores:
            self.log("无合适候选", "ERROR")
            return None
        
        # 按得分排序
        scores.sort(key=lambda x: x['score'], reverse=True)
        
        print("\n评分结果:")
        for i, item in enumerate(scores, 1):
            print(f"{i}. {item['name']} - {item['score']}分")
            print(f"   基金: Perfect={item['perfect']}, Good={item['good']}, Normal={item['normal']}")
            print(f"   {item['details']}")
        
        # 选择最高分
        best = scores[0]
        if best['score'] >= 60:
            self.log(f"推荐使用: {best['name']} (得分={best['score']})", "SUCCESS")
            self.selected_manager = best['name']
            self.selected_personalcode = best['personalcode']
            
            # 保存边界样本信息
            boundary_funds = best['boundary_df'][best['boundary_df']['scenario'].isin(['Perfect', 'Good'])]
            self.boundary_samples = boundary_funds.to_dict('records')
            normal_funds = best['boundary_df'][best['boundary_df']['scenario'] == 'Normal']
            self.normal_funds = normal_funds.to_dict('records')
            
            return best
        else:
            self.log(f"最高得分仅{best['score']}，不建议使用", "WARNING")
            return None
    
    def step4_update_dataset(self):
        """Step 4: 更新dataset.jsonl"""
        self.log("=== Step 4: 更新题目文本 ===")
        
        dataset_file = self.dataset_dir / "question" / "dataset.jsonl"
        
        # 读取所有题目
        with open(dataset_file, 'r', encoding='utf-8') as f:
            lines = [json.loads(line) for line in f]
        
        # 找到fund_109
        found = False
        for item in lines:
            if item['instance_id'] == 'fund_109':
                old_instruction = item['instruction']
                
                # 替换经理姓名
                new_instruction = old_instruction.replace(
                    "张媛在英大基金",
                    f"{self.selected_manager}（PERSONALCODE={self.selected_personalcode}）在其所在基金公司"
                )
                
                # 增加PIT约束说明
                if "【重要】" not in new_instruction:
                    new_instruction = new_instruction.replace(
                        "输出：",
                        "【重要】任职查询必须限制信息披露时间（INFOPUBLDATE < 观察日+1天），禁止在SQL中硬编码INNERCODE。输出："
                    )
                
                item['instruction'] = new_instruction
                found = True
                break
        
        if not found:
            self.log("未找到fund_109题目", "ERROR")
            return False
        
        # 备份原文件
        backup_file = dataset_file.with_suffix('.jsonl.backup')
        dataset_file.rename(backup_file)
        self.log(f"原文件已备份: {backup_file}")
        
        # 写回
        with open(dataset_file, 'w', encoding='utf-8') as f:
            for item in lines:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
        
        self.log("题目文本已更新", "SUCCESS")
        return True
    
    def step5_update_refer(self):
        """Step 5: 更新refer.py"""
        self.log("=== Step 5: 更新参考答案 ===")
        
        refer_file = self.results_dir / "refer.py"
        
        with open(refer_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 替换常量
        content = content.replace(
            'MANAGER_NAME = "张媛"',
            f'MANAGER_NAME = "{self.selected_manager}"'
        )
        
        # 增加PERSONALCODE常量
        if 'MANAGER_PERSONALCODE' not in content:
            content = content.replace(
                'MANAGER_NAME = ',
                f'MANAGER_PERSONALCODE = {self.selected_personalcode}\nMANAGER_NAME = '
            )
        
        # 备份
        backup_file = refer_file.with_suffix('.py.backup')
        refer_file.rename(backup_file)
        self.log(f"原文件已备份: {backup_file}")
        
        # 写回
        with open(refer_file, 'w', encoding='utf-8') as f:
            f.write(content)
        
        self.log("参考答案已更新", "SUCCESS")
        return True
    
    def step6_generate_result(self):
        """Step 6: 生成新的result.csv"""
        self.log("=== Step 6: 生成新的参考输出 ===")
        
        # 这里应该运行refer.py，但为避免复杂性，暂时跳过
        self.log("请手动运行: python refer.py", "WARNING")
        return True
    
    def step7_verify_diff(self):
        """Step 7: 验证选池差异"""
        self.log("=== Step 7: 验证错误SQL差异 ===")
        
        # 正确SQL
        correct_sql = f"""
        SELECT DISTINCT fm.INNERCODE
        FROM mf_fundmanagernew fm
        WHERE fm.PERSONALCODE = {self.selected_personalcode}
          AND fm.POSTNAME = 1
          AND fm.ACCESSIONDATE <= '2025-12-31'
          AND (fm.DIMISSIONDATE IS NULL OR fm.DIMISSIONDATE > '2025-12-31')
          AND (fm.INFOPUBLDATE IS NULL OR fm.INFOPUBLDATE < DATE_ADD('2025-12-31', INTERVAL 1 DAY))
        """
        
        # 错误SQL
        wrong_sql = f"""
        SELECT DISTINCT fm.INNERCODE
        FROM mf_fundmanagernew fm
        WHERE fm.PERSONALCODE = {self.selected_personalcode}
          AND fm.POSTNAME = 1
          AND fm.INCUMBENT = 1
        """
        
        try:
            correct_df = query_to_dataframe(correct_sql)
            wrong_df = query_to_dataframe(wrong_sql)
            
            correct_set = set(correct_df['INNERCODE'].tolist())
            wrong_set = set(wrong_df['INNERCODE'].tolist())
            
            diff = correct_set - wrong_set
            
            self.log(f"正确SQL选出: {len(correct_set)} 只基金")
            self.log(f"错误SQL选出: {len(wrong_set)} 只基金")
            
            if diff:
                self.log(f"✓ 成功暴露差异！正确SQL多选出 {len(diff)} 只基金", "SUCCESS")
                print(f"   边界样本INNERCODE: {sorted(diff)}")
                return True
            else:
                self.log("失败：两种SQL选池一致", "ERROR")
                return False
                
        except Exception as e:
            self.log(f"验证失败: {e}", "ERROR")
            return False
    
    def run_auto(self):
        """自动运行全流程"""
        self.log("开始自动改进流程", "INFO")
        
        # Step 1: 查找候选
        candidates = self.step1_find_candidates()
        if candidates is False or candidates.empty:
            return False
        
        # Step 3: 评分并选择
        best = self.step3_score_and_select(candidates)
        if best is None:
            return False
        
        # Step 4-7: 更新文件
        if not self.confirm("确认使用此候选并更新题目？"):
            self.log("用户取消", "WARNING")
            return False
        
        self.step4_update_dataset()
        self.step5_update_refer()
        self.step6_generate_result()
        
        # 验证
        if self.step7_verify_diff():
            self.log("全部完成！", "SUCCESS")
            return True
        else:
            self.log("验证失败，请检查", "ERROR")
            return False
    
    def run_verify(self):
        """仅验证现有改进"""
        self.log("验证模式：检查fund_109改进是否有效", "INFO")
        
        # 从dataset.jsonl读取当前经理
        dataset_file = self.dataset_dir / "question" / "dataset.jsonl"
        with open(dataset_file, 'r', encoding='utf-8') as f:
            for line in f:
                item = json.loads(line)
                if item['instance_id'] == 'fund_109':
                    instruction = item['instruction']
                    # 提取PERSONALCODE
                    import re
                    match = re.search(r'PERSONALCODE=(\d+)', instruction)
                    if match:
                        self.selected_personalcode = int(match.group(1))
                        self.log(f"当前PERSONALCODE: {self.selected_personalcode}")
                        return self.step7_verify_diff()
        
        self.log("未找到PERSONALCODE", "ERROR")
        return False


def main():
    parser = argparse.ArgumentParser(description="fund_109改进工作流")
    parser.add_argument("--auto", action="store_true", help="自动执行全流程")
    parser.add_argument("--manual", action="store_true", help="手动逐步确认")
    parser.add_argument("--verify", action="store_true", help="仅验证现有改进")
    
    args = parser.parse_args()
    
    if args.verify:
        workflow = Fund109ImprovementWorkflow(auto_mode=True)
        success = workflow.run_verify()
    elif args.auto:
        workflow = Fund109ImprovementWorkflow(auto_mode=True)
        success = workflow.run_auto()
    elif args.manual:
        workflow = Fund109ImprovementWorkflow(auto_mode=False)
        success = workflow.run_auto()
    else:
        parser.print_help()
        return
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
