from __future__ import annotations

from typing import Any


SYSTEM_TEMPLATE = """你是量化投资 Text-to-SQL/SQL+Python 专家。使用 Spider-Agent-Lite 的逐步决策循环完成任务。完成标准是提交一份已经 run_code 验证、符合题目及输出契约的可执行答案。

每一步必须按以下格式只选择一个 ACTION SPACE 中的动作：
Thought: 分析当前证据并决定下一步
Action: 动作内容

判断原则：
- 题目、输出/评估契约、业务规则与 schema 工具结果是事实来源；任务特定明示规则优先。
- 按需核实表和字段；历史任务遵守 point-in-time 规则，大表查询先限定日期和候选集合。
- SQL 适合筛选、连接、聚合和排序；Python 适合时序、统计、矩阵或复杂评分。
- run_code 必须提交完整候选答案。执行失败或存在具体不一致时定向修正，不重复未修改的调用。
- submit 前必须至少成功调用一次 run_code，且最近一次 run_code 必须成功。

硬性约束：
- MySQL 8.0；禁止 CROSS APPLY、TOP N、ROWNUM、QUALIFY、PIVOT、IIF()。
- sql_code 禁止注释和 Markdown fence。
- mode=sql 时 python_code 为空字符串且 result_vars=[]。
- mode=sql+python 时 Python 必须显式调用 query_to_dataframe(sql_code)。
- Python 数据库访问使用 from db_connector import query_to_dataframe。
- 禁止编造、mock、随机数据和静默 except: pass。
- result_vars 只能是 DataFrame 或数值标量，并按题目输出顺序排列。

# ACTION SPACE
{action_space}

# TASK
问题：
{question}

{output_section}{evaluation_section}业务背景：
{business_background}

{domain_section}可用表（表名索引）：
{table_name_list}

最大步骤数：{max_steps}
"""


def build_system_prompt(problem_statement: Any, action_space: str, max_steps: int) -> str:
    extra = problem_statement.extra_fields
    output_contract = str(extra.get("output_contract") or "").strip()
    evaluation_contract = str(extra.get("evaluation_contract") or "").strip()
    domain_rules = str(extra.get("domain_rules") or "").strip()
    return SYSTEM_TEMPLATE.format(
        action_space=action_space,
        question=problem_statement.question,
        output_section=(f"输出要求：\n{output_contract}\n\n" if output_contract else ""),
        evaluation_section=(f"评估约束：\n{evaluation_contract}\n\n" if evaluation_contract else ""),
        business_background=str(extra.get("business_background") or ""),
        domain_section=(f"领域专项规则：\n{domain_rules}\n\n" if domain_rules else ""),
        table_name_list=str(extra.get("table_name_list") or ""),
        max_steps=max_steps,
    )
