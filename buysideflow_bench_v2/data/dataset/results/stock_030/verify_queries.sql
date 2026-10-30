-- stock_030 验证查询脚本
-- 用于验证三个未确认问题：B1（科创板）、A2（历史状态）、A1（ST撤销）

-- ============================================================================
-- Query 1: 检查参考答案12只股票的板块分布
-- ============================================================================
-- 目的：验证是否所有股票都是主板，解释为何科创板遗漏未被检测

SELECT 
    s.SECUCODE AS 代码,
    s.SECUABBR AS 名称,
    s.LISTEDSECTOR AS 板块代码,
    CASE s.LISTEDSECTOR
        WHEN 1 THEN '深圳主板'
        WHEN 2 THEN '上海主板'
        WHEN 7 THEN '科创板'
        WHEN 8 THEN '创业板'
        ELSE '其他'
    END AS 板块名称
FROM secumain s
WHERE s.SECUCODE IN (
    '300641', '002395', '000688', '002648', '000426',
    '603979', '000975', '601168', '600989', '002895',
    '603505', '002170'
)
ORDER BY s.SECUCODE;

-- 预期结果：如果全部 LISTEDSECTOR <> 7，则证明无科创板股票，解释了B1 Pass


-- ============================================================================
-- Query 2: 检查科创板是否有符合财务阈值的"基础化工"或"有色金属"股票
-- ============================================================================
-- 目的：验证是否存在被模型遗漏但应被包含的科创板股票

WITH params AS (
    SELECT TIMESTAMP('2025-07-25 23:59:59') AS as_of_dt
),
stib_stocks AS (
    SELECT 
        s.INNERCODE,
        s.COMPANYCODE,
        s.SECUCODE,
        s.SECUABBR,
        s.LISTEDSECTOR
    FROM secumain s
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET = 90  -- 科创板
      AND s.LISTEDSECTOR = 7
      AND s.LISTEDDATE <= (SELECT as_of_dt FROM params)
),
stib_industry AS (
    SELECT 
        e.COMPANYCODE,
        e.FIRSTINDUSTRYNAME,
        e.SECONDINDUSTRYNAME,
        e.INFOPUBLDATE,
        ROW_NUMBER() OVER (
            PARTITION BY e.COMPANYCODE
            ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
        ) AS rn
    FROM lc_stibexgindustry e
    JOIN stib_stocks ss ON ss.COMPANYCODE = e.COMPANYCODE
    JOIN params p ON e.INFOPUBLDATE <= p.as_of_dt
    WHERE e.STANDARD = 38
      AND e.EFFECTIVEDATE <= p.as_of_dt
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_dt)
),
stib_finance AS (
    SELECT 
        m.COMPANYCODE,
        m.ENDDATE,
        m.INFOPUBLDATE,
        m.ROETTM,
        m.OPERATINGREVENUEYOY,
        m.NPPARENTCOMPANYYOY,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY CASE WHEN m.IFADJUSTED = 1 THEN 0 ELSE 1 END,
                     m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn_ver
    FROM lc_stibmainindex m
    JOIN stib_stocks ss ON ss.COMPANYCODE = m.COMPANYCODE
    JOIN params p ON m.INFOPUBLDATE <= p.as_of_dt
    WHERE m.ENDDATE <= p.as_of_dt
      AND m.IFMERGED = 1
),
latest_stib_finance AS (
    SELECT 
        COMPANYCODE,
        ENDDATE,
        ROETTM,
        OPERATINGREVENUEYOY,
        NPPARENTCOMPANYYOY,
        ROW_NUMBER() OVER (
            PARTITION BY COMPANYCODE
            ORDER BY ENDDATE DESC
        ) AS rn_latest
    FROM stib_finance
    WHERE rn_ver = 1
)
SELECT 
    ss.SECUCODE AS 代码,
    ss.SECUABBR AS 名称,
    si.FIRSTINDUSTRYNAME AS 申万一级行业,
    si.SECONDINDUSTRYNAME AS 申万二级行业,
    ROUND(lf.OPERATINGREVENUEYOY, 2) AS 营收同比增速,
    ROUND(lf.ROETTM, 2) AS TTM_ROE,
    ROUND(lf.NPPARENTCOMPANYYOY, 2) AS 净利润同比,
    lf.ENDDATE AS 最新报告期
FROM stib_stocks ss
JOIN stib_industry si ON si.COMPANYCODE = ss.COMPANYCODE AND si.rn = 1
JOIN latest_stib_finance lf ON lf.COMPANYCODE = ss.COMPANYCODE AND lf.rn_latest = 1
WHERE si.FIRSTINDUSTRYNAME IN ('基础化工', '有色金属')
  AND lf.ROETTM > 15
  AND lf.OPERATINGREVENUEYOY > 30
  AND lf.NPPARENTCOMPANYYOY > 0
ORDER BY lf.ROETTM DESC;

-- 预期结果：
-- 如果返回0行 → 科创板无符合条件股票，模型遗漏不影响结果
-- 如果返回>0行 → 科创板有符合条件股票被模型遗漏，评测应该Fail


-- ============================================================================
-- Query 3: 国城矿业（000688）的行业分类历史
-- ============================================================================
-- 目的：验证是否存在行业变更，解释A2问题

SELECT 
    e.COMPANYCODE,
    e.FIRSTINDUSTRYNAME AS 一级行业,
    e.SECONDINDUSTRYNAME AS 二级行业,
    e.INFOPUBLDATE AS 信息发布日期,
    e.CANCELDATE AS 取消日期,
    e.IFPERFORMED AS 是否有效,
    e.EFFECTIVEDATE AS 生效日期,
    DATEDIFF(
        COALESCE(e.CANCELDATE, '2025-07-25'), 
        e.EFFECTIVEDATE
    ) AS 有效天数
FROM lc_exgindustry e
JOIN secumain s ON s.COMPANYCODE = e.COMPANYCODE
WHERE s.SECUCODE = '000688'
  AND e.STANDARD = 38
ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
LIMIT 20;

-- 预期分析：
-- 如果只有1条"有色金属"记录 → 无行业变更，A2问题是数据巧合
-- 如果有多条且FIRSTINDUSTRYNAME不同 → 存在行业变更，A2是真实风险


-- ============================================================================
-- Query 4: 国城矿业的上市状态历史
-- ============================================================================
-- 目的：验证 LISTEDSTATE=1 是否等同于时点约束

SELECT 
    ls.INNERCODE,
    s.SECUCODE,
    s.SECUABBR,
    ls.CHANGEDATE AS 变更日期,
    ls.CHANGETYPE AS 变更类型,
    CASE ls.CHANGETYPE
        WHEN 1 THEN '上市'
        WHEN 2 THEN '暂停上市'
        WHEN 3 THEN '恢复上市'
        WHEN 4 THEN '终止上市'
        WHEN 5 THEN '其他'
        ELSE '未知'
    END AS 变更类型说明,
    ls.INSERTTIME AS 插入时间,
    s.LISTEDSTATE AS 当前上市状态
FROM lc_liststatus ls
JOIN secumain s ON s.INNERCODE = ls.INNERCODE
WHERE s.SECUCODE = '000688'
ORDER BY ls.CHANGEDATE DESC, ls.INSERTTIME DESC
LIMIT 20;

-- 预期分析：
-- 如果只有1条CHANGETYPE=1 → 从未变更，LISTEDSTATE=1 等同于时点约束
-- 如果有多条记录 → 存在状态变更历史，需要时点约束


-- ============================================================================
-- Query 5: 检查所有参考答案股票的上市状态历史
-- ============================================================================
-- 目的：全面检查12只股票是否存在状态变更

WITH ref_stocks AS (
    SELECT SECUCODE FROM (
        SELECT '300641' AS SECUCODE UNION ALL SELECT '002395' UNION ALL
        SELECT '000688' UNION ALL SELECT '002648' UNION ALL SELECT '000426' UNION ALL
        SELECT '603979' UNION ALL SELECT '000975' UNION ALL SELECT '601168' UNION ALL
        SELECT '600989' UNION ALL SELECT '002895' UNION ALL
        SELECT '603505' UNION ALL SELECT '002170'
    ) t
)
SELECT 
    s.SECUCODE AS 代码,
    s.SECUABBR AS 名称,
    COUNT(DISTINCT ls.CHANGETYPE) AS 状态变更类型数,
    COUNT(*) AS 变更记录数,
    MAX(ls.CHANGEDATE) AS 最后变更日期,
    s.LISTEDSTATE AS 当前状态
FROM ref_stocks rs
JOIN secumain s ON s.SECUCODE = rs.SECUCODE
LEFT JOIN lc_liststatus ls ON ls.INNERCODE = s.INNERCODE
GROUP BY s.SECUCODE, s.SECUABBR, s.LISTEDSTATE
ORDER BY 状态变更类型数 DESC, 变更记录数 DESC;

-- 预期分析：
-- 状态变更类型数=1 且 变更记录数=1 → 无历史变更
-- 状态变更类型数>1 或 变更记录数>1 → 存在历史变更，需要时点约束


-- ============================================================================
-- Query 6: ST撤销迟公告案例检查
-- ============================================================================
-- 目的：验证是否存在 REMOVEDATE <= 2025-07-25 但 REMOVEINFOPUBLDATE > 2025-07-25 的记录

SELECT 
    ra.INNERCODE,
    s.SECUCODE AS 代码,
    s.SECUABBR AS 名称,
    ra.RISKALERTTYPE AS 风险类型,
    CASE ra.RISKALERTTYPE
        WHEN 1 THEN 'ST'
        WHEN 5 THEN '*ST'
        WHEN 12 THEN '退市风险警示'
        WHEN 14 THEN '其他风险警示'
        ELSE '其他'
    END AS 风险类型说明,
    DATE(ra.IMPLEMENTDATE) AS 实施日期,
    DATE(ra.IMPLANNOUCEDATE) AS 实施公告日期,
    DATE(ra.REMOVEDATE) AS 撤销日期,
    DATE(ra.REMOVEINFOPUBLDATE) AS 撤销公告日期,
    DATEDIFF(ra.REMOVEINFOPUBLDATE, ra.REMOVEDATE) AS 撤销公告延迟天数,
    ra.INSERTTIME AS 记录插入时间
FROM cs_riskalert ra
JOIN secumain s ON s.INNERCODE = ra.INNERCODE
WHERE ra.RISKALERTTYPE IN (1, 5, 12, 14)
  AND ra.REMOVEDATE IS NOT NULL
  AND ra.REMOVEINFOPUBLDATE IS NOT NULL
  AND DATE(ra.REMOVEDATE) <= DATE('2025-07-25')
  AND DATE(ra.REMOVEINFOPUBLDATE) > DATE('2025-07-25')
ORDER BY 撤销公告延迟天数 DESC
LIMIT 10;

-- 预期结果：
-- 如果返回0行 → 不存在撤销迟公告，A1逻辑分支未覆盖（但逻辑正确）
-- 如果返回>0行 → 存在撤销迟公告，需要验证是否有符合财务条件的股票被错误剔除


-- ============================================================================
-- Query 7: 全局ST撤销公告延迟统计
-- ============================================================================
-- 目的：了解ST撤销公告的整体延迟情况

SELECT 
    CASE 
        WHEN DATEDIFF(REMOVEINFOPUBLDATE, REMOVEDATE) = 0 THEN '当天公告'
        WHEN DATEDIFF(REMOVEINFOPUBLDATE, REMOVEDATE) BETWEEN 1 AND 3 THEN '1-3天'
        WHEN DATEDIFF(REMOVEINFOPUBLDATE, REMOVEDATE) BETWEEN 4 AND 7 THEN '4-7天'
        WHEN DATEDIFF(REMOVEINFOPUBLDATE, REMOVEDATE) BETWEEN 8 AND 30 THEN '8-30天'
        WHEN DATEDIFF(REMOVEINFOPUBLDATE, REMOVEDATE) > 30 THEN '>30天'
        WHEN DATEDIFF(REMOVEINFOPUBLDATE, REMOVEDATE) < 0 THEN '提前公告'
        ELSE '未知'
    END AS 公告延迟区间,
    COUNT(*) AS 记录数,
    ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) AS 占比
FROM cs_riskalert
WHERE RISKALERTTYPE IN (1, 5, 12, 14)
  AND REMOVEDATE IS NOT NULL
  AND REMOVEINFOPUBLDATE IS NOT NULL
GROUP BY 公告延迟区间
ORDER BY 记录数 DESC;

-- 预期分析：了解撤销公告延迟的分布情况


-- ============================================================================
-- Query 8: 检查参考答案12只股票的ST历史
-- ============================================================================
-- 目的：验证这些股票是否曾经是ST或已撤销ST

WITH ref_stocks AS (
    SELECT SECUCODE FROM (
        SELECT '300641' AS SECUCODE UNION ALL SELECT '002395' UNION ALL
        SELECT '000688' UNION ALL SELECT '002648' UNION ALL SELECT '000426' UNION ALL
        SELECT '603979' UNION ALL SELECT '000975' UNION ALL SELECT '601168' UNION ALL
        SELECT '600989' UNION ALL SELECT '002895' UNION ALL
        SELECT '603505' UNION ALL SELECT '002170'
    ) t
)
SELECT 
    s.SECUCODE AS 代码,
    s.SECUABBR AS 名称,
    COUNT(*) AS ST记录数,
    MAX(DATE(ra.IMPLEMENTDATE)) AS 最后实施日期,
    MAX(DATE(ra.REMOVEDATE)) AS 最后撤销日期,
    SUM(CASE WHEN ra.REMOVEDATE IS NULL THEN 1 ELSE 0 END) AS 未撤销数量
FROM ref_stocks rs
JOIN secumain s ON s.SECUCODE = rs.SECUCODE
LEFT JOIN cs_riskalert ra ON ra.INNERCODE = s.INNERCODE 
    AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
GROUP BY s.SECUCODE, s.SECUABBR
HAVING COUNT(*) > 0
ORDER BY ST记录数 DESC;

-- 预期结果：
-- 如果返回0行 → 12只股票从未ST，ST剔除逻辑未被有效测试
-- 如果返回>0行 → 存在曾经ST的股票，验证撤销逻辑的重要性


-- ============================================================================
-- Query 9: 检查是否有股票同时符合财务条件但因ST被剔除
-- ============================================================================
-- 目的：验证ST剔除逻辑的必要性

WITH params AS (
    SELECT TIMESTAMP('2025-07-25 23:59:59') AS as_of_dt
),
st_stocks AS (
    SELECT DISTINCT ra.INNERCODE
    FROM cs_riskalert ra
    JOIN params p ON 
        DATE(ra.IMPLEMENTDATE) <= DATE(p.as_of_dt)
        AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= p.as_of_dt
        AND (
            ra.REMOVEDATE IS NULL
            OR DATE(ra.REMOVEDATE) > DATE(p.as_of_dt)
            OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) > p.as_of_dt
        )
    WHERE ra.RISKALERTTYPE IN (1, 5, 12, 14)
),
qualified_but_st AS (
    SELECT 
        s.SECUCODE AS 代码,
        s.SECUABBR AS 名称,
        e.FIRSTINDUSTRYNAME AS 一级行业,
        e.SECONDINDUSTRYNAME AS 二级行业,
        ROUND(m.OPERATINGREVENUEGROWRATE, 2) AS 营收增速,
        ROUND(m.ROETTM, 2) AS TTM_ROE,
        m.NPPARENTCOMPANYYOY AS 净利润同比
    FROM secumain s
    JOIN st_stocks st ON st.INNERCODE = s.INNERCODE
    JOIN lc_exgindustry e ON e.COMPANYCODE = s.COMPANYCODE
    JOIN lc_mainindexnew m ON m.COMPANYCODE = s.COMPANYCODE
    JOIN params p ON 
        e.INFOPUBLDATE <= p.as_of_dt
        AND m.INFOPUBLDATE <= p.as_of_dt
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND e.STANDARD = 38
      AND e.FIRSTINDUSTRYNAME IN ('基础化工', '有色金属')
      AND m.ROETTM > 15
      AND m.OPERATINGREVENUEGROWRATE > 30
      AND m.NPPARENTCOMPANYYOY > 0
    LIMIT 10
)
SELECT * FROM qualified_but_st;

-- 预期结果：
-- 如果返回>0行 → 证明ST剔除逻辑是必要的，有股票因ST被正确剔除
-- 如果返回0行 → ST剔除未影响结果


-- ============================================================================
-- Query 10: 汇总统计
-- ============================================================================
-- 目的：一览式统计关键指标

SELECT 
    '参考答案股票板块分布' AS 统计项,
    COUNT(*) AS 总数,
    SUM(CASE WHEN s.LISTEDSECTOR = 7 THEN 1 ELSE 0 END) AS 科创板数量,
    SUM(CASE WHEN s.LISTEDSECTOR <> 7 THEN 1 ELSE 0 END) AS 非科创板数量
FROM secumain s
WHERE s.SECUCODE IN (
    '300641', '002395', '000688', '002648', '000426',
    '603979', '000975', '601168', '600989', '002895',
    '603505', '002170'
)
UNION ALL
SELECT 
    '科创板符合条件股票数' AS 统计项,
    0 AS 总数,
    0 AS 科创板数量,
    0 AS 非科创板数量
-- (需要执行Query 2后手动填充)
UNION ALL
SELECT 
    'ST撤销迟公告案例数' AS 统计项,
    0 AS 总数,
    0 AS 科创板数量,
    0 AS 非科创板数量
-- (需要执行Query 6后手动填充)
;

-- ============================================================================
-- 使用说明
-- ============================================================================
-- 1. 依次执行Query 1-10
-- 2. 记录每个查询的结果
-- 3. 根据结果更新ERROR_ANALYSIS.md中的"验证结果"章节
-- 4. 决定是否需要修改题目或创建variant

-- 关键判断：
-- - Query 1 结果全部非科创板 → B1是数据巧合
-- - Query 2 返回0行 → B1不影响结果
-- - Query 3 显示行业变更 → A2是真实风险
-- - Query 6 返回0行 → A1逻辑分支未覆盖但正确
