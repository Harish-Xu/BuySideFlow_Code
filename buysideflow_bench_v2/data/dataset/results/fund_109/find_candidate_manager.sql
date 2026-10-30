-- =========================================
-- fund_109 候选基金经理查询脚本
-- 目标：找到能替代张媛的基金经理，暴露INCUMBENT=1错误
-- =========================================

-- Step 1: 找到管理多只主动权益产品的大经理
-- 要求：管理>=5只符合条件的基金，且近期有>=2次离任记录

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
    END) as recent_departures,
    MIN(fa.INVESTADVISORCODE) as primary_advisor_code,
    GROUP_CONCAT(DISTINCT ic.COMPANYABBR SEPARATOR ' | ') as companies
FROM mf_fundmanagernew fm
JOIN mf_fundarchives fa ON fm.INNERCODE = fa.INNERCODE
LEFT JOIN mf_investadvisoroutline ic ON fa.INVESTADVISORCODE = ic.COMPANYCODE
JOIN mf_jyfundtype ft ON fm.INNERCODE = ft.INNERCODE
WHERE fm.POSTNAME = 1
  AND fa.INVESTMENTTYPE = 16  -- 主动管理
  AND ft.STANDARD = 75
  AND ft.SECASSETCATCODE IN ('1101','1201','1202','1203')  -- 主动权益
  AND COALESCE(fa.IFFOF, 2) <> 1  -- 非FOF
  AND COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE  -- 主份额
  AND fa.ESTABLISHMENTDATE < '2023-01-01'
  AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= '2025-12-31')
GROUP BY fm.NAME, fm.PERSONALCODE
HAVING total_funds >= 5
  AND funds_at_anchor >= 3
  AND recent_departures >= 2
ORDER BY 
    recent_departures DESC,
    funds_at_anchor DESC,
    total_funds DESC
LIMIT 20;


-- Step 2: 对候选经理，检查"边界样本"分布
-- 替换 '<CANDIDATE_NAME>' 为Step 1返回的经理姓名

SELECT 
    fm.INNERCODE,
    fa.SECURITYCODE as fund_code,
    sm.SECUABBR as fund_name,
    fm.ACCESSIONDATE as join_date,
    fm.DIMISSIONDATE as leave_date,
    fm.INFOPUBLDATE as publish_date,
    fm.INCUMBENT as is_incumbent,
    fa.ESTABLISHMENTDATE as establish_date,
    fa.EXPIREDATE as expire_date,
    CASE 
        -- Perfect: 锚点时在任，但锚点后才公告离任
        WHEN fm.ACCESSIONDATE <= '2025-12-31'
         AND fm.DIMISSIONDATE > '2025-12-31'
         AND (fm.INFOPUBLDATE IS NULL OR fm.INFOPUBLDATE >= '2026-01-01')
        THEN '⭐⭐⭐ Perfect: 锚点后离任+公告晚'
        
        -- Good: 锚点前已离任，但公告日晚于锚点
        WHEN fm.ACCESSIONDATE <= '2025-12-31'
         AND fm.DIMISSIONDATE > '2023-01-01'  -- 至少在计算区间内任职过
         AND fm.DIMISSIONDATE <= '2025-12-31'
         AND fm.INFOPUBLDATE >= '2026-01-01'
        THEN '⭐⭐ Good: 锚点前离任但公告晚'
        
        -- Normal: 锚点时仍在任，且无离任记录或离任公告及时
        WHEN fm.ACCESSIONDATE <= '2025-12-31'
         AND (fm.DIMISSIONDATE IS NULL OR fm.DIMISSIONDATE > '2025-12-31')
         AND (fm.INFOPUBLDATE IS NULL OR fm.INFOPUBLDATE < '2026-01-01')
        THEN '✓ Normal: 锚点时在任'
        
        -- Useless: 锚点后才上任
        WHEN fm.ACCESSIONDATE > '2025-12-31'
        THEN '✗ Useless: 锚点后上任'
        
        -- Irrelevant: 计算区间前就离任
        WHEN fm.DIMISSIONDATE < '2023-01-01'
        THEN '✗ Irrelevant: 区间前已离任'
        
        ELSE '? Unknown scenario'
    END as boundary_scenario,
    CASE 
        WHEN fm.ACCESSIONDATE <= '2025-12-31'
         AND (fm.DIMISSIONDATE IS NULL OR fm.DIMISSIONDATE > '2025-12-31')
        THEN 'Y'
        ELSE 'N'
    END as in_correct_pool,
    CASE 
        WHEN fm.INCUMBENT = 1
        THEN 'Y'
        ELSE 'N'
    END as in_wrong_pool
FROM mf_fundmanagernew fm
JOIN mf_fundarchives fa ON fm.INNERCODE = fa.INNERCODE
JOIN secumain sm ON fm.INNERCODE = sm.INNERCODE
JOIN mf_jyfundtype ft ON fm.INNERCODE = ft.INNERCODE
WHERE fm.NAME = '<CANDIDATE_NAME>'  -- 替换为实际候选经理姓名
  AND fm.POSTNAME = 1
  AND fa.INVESTMENTTYPE = 16
  AND ft.STANDARD = 75
  AND ft.SECASSETCATCODE IN ('1101','1201','1202','1203')
  AND COALESCE(fa.IFFOF, 2) <> 1
  AND COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
  AND fa.ESTABLISHMENTDATE < '2023-01-01'
  AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= '2025-12-31')
ORDER BY 
    FIELD(boundary_scenario, 
          '⭐⭐⭐ Perfect: 锚点后离任+公告晚',
          '⭐⭐ Good: 锚点前离任但公告晚',
          '✓ Normal: 锚点时在任',
          '✗ Useless: 锚点后上任',
          '✗ Irrelevant: 区间前已离任'),
    fm.DIMISSIONDATE DESC;


-- Step 3: 验证净值数据完整性
-- 对Step 2返回的基金，检查2023-2025净值覆盖率

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
WHERE nv.INNERCODE IN (
    -- 将Step 2返回的INNERCODE列表粘贴到这里
    -- 例如: 12345, 67890, 11111
    <PASTE_INNERCODE_LIST>
)
  AND nv.TRADINGDAY BETWEEN '2023-01-01' AND '2025-12-31'
  AND nv.UNITNVRESTORED IS NOT NULL
GROUP BY nv.INNERCODE, fa.SECURITYCODE, sm.SECUABBR
HAVING nav_count >= 500  -- 至少500个净值点（约2年日度数据）
ORDER BY coverage_pct DESC;


-- Step 4: 最终评分（综合Step 2和Step 3的结果）
-- 手工评分标准：

/*
候选经理评分卡：

【必需条件】（不满足直接淘汰）
□ 锚点时在任基金数 >= 3
□ 存在至少1只"Perfect"或"Good"边界样本
□ 所有在任基金的净值覆盖率 >= 80%

【加分项】（按重要性排序）
+50分: 有1只"Perfect"边界样本
+30分: 有2+只"Perfect"或"Good"边界样本
+20分: 锚点时在任基金数 = 3-5（适中规模）
+10分: 单一基金公司（INVESTADVISORCODE一致）
+10分: 净值覆盖率 > 95%
-10分: 锚点时在任基金数 > 8（过多）
-20分: 跨多个基金公司（难以限制ADVISOR）

【目标分数】
>= 80分: 优秀候选，强烈推荐
60-79分: 可用候选，建议使用
40-59分: 勉强可用，需调整题目条件
< 40分: 不推荐，继续寻找其他候选

【示例评分】
经理A: 
  - 4只在任基金 (+20)
  - 1只Perfect边界样本 (+50)
  - 净值覆盖率98% (+10)
  - 单一公司 (+10)
  = 90分 ✓ 优秀

经理B:
  - 6只在任基金 (+20)
  - 2只Good边界样本 (+30)
  - 净值覆盖率85% (+0)
  - 跨3个公司 (-20)
  = 30分 ✗ 不推荐
*/


-- =========================================
-- 使用说明
-- =========================================

/*
1. 先执行 Step 1，获取候选经理列表（预计10-20位）

2. 对Top 5候选，逐一执行 Step 2：
   - 替换 <CANDIDATE_NAME> 为实际姓名
   - 观察 boundary_scenario 列的分布
   - 重点关注"Perfect"和"Good"样本的数量

3. 对有边界样本的候选，执行 Step 3：
   - 粘贴Step 2返回的INNERCODE列表
   - 检查净值覆盖率是否 >= 80%

4. 手工评分（Step 4），选出得分最高的1位经理

5. 用选出的经理更新fund_109题目：
   - 更新MANAGER_NAME和PERSONALCODE
   - 更新INVESTADVISORCODE（或移除该约束）
   - 重新生成refer.py和result.csv

6. 验证错误SQL输出差异：
   - 正确SQL（含INFOPUBLDATE约束）
   - 错误SQL（INCUMBENT=1，无INFOPUBLDATE）
   - 确认两者输出不同（边界样本应只在正确SQL中出现）
*/
