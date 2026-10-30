-- stock_030 数据验证脚本
-- 目标：验证为什么模型有错误标记却能Pass

-- ============================================================
-- 验证 A1: ST 撤销公告时点问题
-- ============================================================

-- 检查12只参考股票的ST历史（重点关注撤销迟公告）
SELECT 
    'A1: ST撤销公告验证' AS verification_type,
    s.SECUCODE AS 代码,
    s.SECUABBR AS 名称,
    ra.RISKALERTTYPE AS 风险类型,
    ra.IMPLEMENTDATE AS 实施日期,
    ra.IMPLANNOUCEDATE AS 实施公告日期,
    ra.REMOVEDATE AS 撤销日期,
    ra.REMOVEINFOPUBLDATE AS 撤销公告日期,
    ra.INSERTTIME AS 维护时间,
    CASE 
        WHEN ra.REMOVEDATE IS NOT NULL 
         AND DATE(ra.REMOVEDATE) <= '2025-07-25'
         AND COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) > TIMESTAMP('2025-07-25 23:59:59')
        THEN '撤销迟公告-观察日看似ST但实际已撤销'
        WHEN ra.REMOVEDATE IS NULL 
         AND DATE(ra.IMPLEMENTDATE) <= '2025-07-25'
         AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= TIMESTAMP('2025-07-25 23:59:59')
        THEN '观察日时为ST状态'
        WHEN DATE(ra.IMPLEMENTDATE) > '2025-07-25'
        THEN '实施日期在观察日后-不影响'
        ELSE '其他情况'
    END AS ST状态判定
FROM secumain s
JOIN cs_riskalert ra ON ra.INNERCODE = s.INNERCODE
WHERE s.SECUCODE IN (
    '300641', '002395', '000688', '002648', '000426',
    '603979', '000975', '601168', '600989', '002895',
    '603505', '002170'
)
AND ra.RISKALERTTYPE IN (1, 5, 12, 14)  -- ST相关风险类型
ORDER BY s.SECUCODE, ra.IMPLEMENTDATE DESC;

-- ============================================================
-- 验证 A2: 行业分类时点问题（重点关注国城矿业000688）
-- ============================================================

-- 检查国城矿业的行业分类历史
SELECT 
    'A2: 国城矿业行业分类历史' AS verification_type,
    s.SECUCODE AS 代码,
    s.SECUABBR AS 名称,
    e.FIRSTINDUSTRYNAME AS 申万一级行业,
    e.SECONDINDUSTRYNAME AS 申万二级行业,
    e.INFOPUBLDATE AS 信息发布日期,
    e.EFFECTIVEDATE AS 生效日期,
    e.CANCELDATE AS 取消日期,
    e.INSERTTIME AS 维护时间,
    CASE 
        WHEN e.INFOPUBLDATE <= TIMESTAMP('2025-07-25 23:59:59')
         AND (e.CANCELDATE IS NULL OR e.CANCELDATE > TIMESTAMP('2025-07-25 23:59:59'))
        THEN '观察日有效'
        WHEN e.INFOPUBLDATE > TIMESTAMP('2025-07-25 23:59:59')
        THEN '观察日未公告'
        ELSE '已取消'
    END AS 分类状态
FROM secumain s
JOIN lc_exgindustry e ON e.COMPANYCODE = s.COMPANYCODE
WHERE s.SECUCODE = '000688'
  AND e.STANDARD = 38  -- 申万行业分类
ORDER BY e.INFOPUBLDATE DESC
LIMIT 10;

-- 检查所有12只股票的行业分类，看是否有时点冲突
SELECT 
    'A2: 所有股票行业时点验证' AS verification_type,
    s.SECUCODE AS 代码,
    s.SECUABBR AS 名称,
    COUNT(DISTINCT e.FIRSTINDUSTRYNAME) AS 一级行业变更次数,
    COUNT(DISTINCT e.SECONDINDUSTRYNAME) AS 二级行业变更次数,
    MAX(e.INFOPUBLDATE) AS 最新行业公告日期,
    SUM(CASE WHEN e.INFOPUBLDATE > TIMESTAMP('2025-07-25 23:59:59') THEN 1 ELSE 0 END) AS 观察日后的公告数
FROM secumain s
JOIN lc_exgindustry e ON e.COMPANYCODE = s.COMPANYCODE
WHERE s.SECUCODE IN (
    '300641', '002395', '000688', '002648', '000426',
    '603979', '000975', '601168', '600989', '002895',
    '603505', '002170'
)
AND e.STANDARD = 38
GROUP BY s.SECUCODE, s.SECUABBR
ORDER BY 观察日后的公告数 DESC, s.SECUCODE;

-- ============================================================
-- 验证 A2: 上市状态时点问题（重点关注国城矿业000688）
-- ============================================================

-- 检查国城矿业的上市状态变更历史
SELECT 
    'A2: 国城矿业上市状态历史' AS verification_type,
    s.SECUCODE AS 代码,
    s.SECUABBR AS 名称,
    ls.CHANGETYPE AS 变动类型,
    ls.CHANGEDATE AS 变动日期,
    ls.INSERTTIME AS 维护时间,
    ls.CHANGEEXPLAIN AS 变动说明,
    CASE 
        WHEN ls.CHANGEDATE <= TIMESTAMP('2025-07-25 23:59:59')
         AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME <= TIMESTAMP('2025-07-25 23:59:59'))
        THEN '观察日已知'
        WHEN ls.CHANGEDATE <= TIMESTAMP('2025-07-25 23:59:59')
         AND ls.INSERTTIME > TIMESTAMP('2025-07-25 23:59:59')
        THEN '变动在观察日前但维护在观察日后'
        ELSE '观察日后发生'
    END AS 时点状态
FROM secumain s
JOIN lc_liststatus ls ON ls.INNERCODE = s.INNERCODE
WHERE s.SECUCODE = '000688'
  AND ls.SECUMARKET IN (83, 90)
  AND ls.CHANGETYPE BETWEEN 1 AND 9
ORDER BY ls.CHANGEDATE DESC
LIMIT 10;

-- 检查所有12只股票的上市状态，看是否有时点冲突
SELECT 
    'A2: 所有股票上市状态验证' AS verification_type,
    s.SECUCODE AS 代码,
    s.SECUABBR AS 名称,
    COUNT(*) AS 状态变更次数,
    MAX(ls.CHANGEDATE) AS 最新变更日期,
    SUM(CASE 
        WHEN ls.CHANGEDATE <= TIMESTAMP('2025-07-25 23:59:59')
         AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME <= TIMESTAMP('2025-07-25 23:59:59'))
        THEN 1 ELSE 0 
    END) AS 观察日已知的变更数,
    SUM(CASE 
        WHEN ls.CHANGEDATE <= TIMESTAMP('2025-07-25 23:59:59')
         AND ls.INSERTTIME > TIMESTAMP('2025-07-25 23:59:59')
        THEN 1 ELSE 0 
    END) AS 时点冲突数
FROM secumain s
JOIN lc_liststatus ls ON ls.INNERCODE = s.INNERCODE
WHERE s.SECUCODE IN (
    '300641', '002395', '000688', '002648', '000426',
    '603979', '000975', '601168', '600989', '002895',
    '603505', '002170'
)
AND ls.SECUMARKET IN (83, 90)
AND ls.CHANGETYPE BETWEEN 1 AND 9
GROUP BY s.SECUCODE, s.SECUABBR
ORDER BY 时点冲突数 DESC, s.SECUCODE;

-- ============================================================
-- 验证 B1: 科创板专表覆盖
-- ============================================================

-- 检查12只股票中是否有科创板股票
SELECT 
    'B1: 板块类型验证' AS verification_type,
    s.SECUCODE AS 代码,
    s.SECUABBR AS 名称,
    s.LISTEDSECTOR AS 上市板块代码,
    s.SECUMARKET AS 交易所代码,
    CASE 
        WHEN s.LISTEDSECTOR = 7 THEN '科创板'
        WHEN s.SECUMARKET = 90 THEN '上交所主板'
        WHEN s.SECUMARKET = 83 THEN '深交所主板/中小板/创业板'
        ELSE '其他'
    END AS 板块类型,
    CASE 
        WHEN s.SECUCODE LIKE '688%' THEN '是'
        ELSE '否'
    END AS 代码特征是否科创板
FROM secumain s
WHERE s.SECUCODE IN (
    '300641', '002395', '000688', '002648', '000426',
    '603979', '000975', '601168', '600989', '002895',
    '603505', '002170'
)
ORDER BY s.LISTEDSECTOR, s.SECUCODE;

-- 检查是否有科创板股票需要查询lc_stibmainindex
SELECT 
    'B1: 科创板财务数据表覆盖验证' AS verification_type,
    s.SECUCODE AS 代码,
    s.SECUABBR AS 名称,
    COUNT(DISTINCT m.ENDDATE) AS 科创板专表报告期数,
    MAX(m.ENDDATE) AS 最新报告期
FROM secumain s
JOIN lc_stibmainindex m ON m.COMPANYCODE = s.COMPANYCODE
WHERE s.SECUCODE IN (
    '300641', '002395', '000688', '002648', '000426',
    '603979', '000975', '601168', '600989', '002895',
    '603505', '002170'
)
AND s.LISTEDSECTOR = 7
AND m.IFMERGED = 1
AND m.ENDDATE <= TIMESTAMP('2025-07-25 23:59:59')
AND m.INFOPUBLDATE <= TIMESTAMP('2025-07-25 23:59:59')
GROUP BY s.SECUCODE, s.SECUABBR;

-- ============================================================
-- 验证数据巧合：检查错误逻辑和正确逻辑的输出差异
-- ============================================================

-- 错误逻辑1: 使用LISTEDSTATE=1（当前上市状态）
WITH wrong_logic_listed AS (
    SELECT 
        s.SECUCODE,
        s.SECUABBR
    FROM secumain s
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSTATE = 1  -- ❌ 错误：用当前状态
      AND s.LISTEDDATE <= TIMESTAMP('2025-07-25 23:59:59')
      AND s.SECUCODE IN (
          '300641', '002395', '000688', '002648', '000426',
          '603979', '000975', '601168', '600989', '002895',
          '603505', '002170'
      )
),
-- 正确逻辑: 重建历史上市状态
correct_logic_listed AS (
    SELECT 
        s.SECUCODE,
        s.SECUABBR
    FROM secumain s
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDDATE <= TIMESTAMP('2025-07-25 23:59:59')
      AND s.SECUCODE IN (
          '300641', '002395', '000688', '002648', '000426',
          '603979', '000975', '601168', '600989', '002895',
          '603505', '002170'
      )
      AND EXISTS (
          SELECT 1
          FROM (
              SELECT INNERCODE, CHANGETYPE, CHANGEDATE, INSERTTIME
              FROM lc_liststatus
              WHERE SECUMARKET IN (83, 90)
                AND CHANGETYPE BETWEEN 1 AND 9
              UNION ALL
              SELECT INNERCODE, CHANGETYPE, CHANGEDATE, INSERTTIME, 83 AS SECUMARKET
              FROM lc_stibliststatus
              WHERE CHANGETYPE BETWEEN 1 AND 9
          ) ls
          WHERE ls.INNERCODE = s.INNERCODE
            AND ls.CHANGEDATE <= TIMESTAMP('2025-07-25 23:59:59')
            AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME <= TIMESTAMP('2025-07-25 23:59:59'))
            AND ls.CHANGETYPE IN (1, 3)  -- 上市、恢复上市
          ORDER BY ls.CHANGEDATE DESC, COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC
          LIMIT 1
      )
)
SELECT 
    '数据巧合验证: 上市状态逻辑对比' AS verification_type,
    COALESCE(w.SECUCODE, c.SECUCODE) AS 代码,
    COALESCE(w.SECUABBR, c.SECUABBR) AS 名称,
    CASE WHEN w.SECUCODE IS NOT NULL THEN '是' ELSE '否' END AS 错误逻辑是否包含,
    CASE WHEN c.SECUCODE IS NOT NULL THEN '是' ELSE '否' END AS 正确逻辑是否包含,
    CASE 
        WHEN w.SECUCODE IS NOT NULL AND c.SECUCODE IS NOT NULL THEN '一致-数据巧合'
        WHEN w.SECUCODE IS NULL AND c.SECUCODE IS NOT NULL THEN '错误逻辑遗漏'
        WHEN w.SECUCODE IS NOT NULL AND c.SECUCODE IS NULL THEN '错误逻辑多选'
        ELSE '都不包含'
    END AS 差异分析
FROM wrong_logic_listed w
FULL OUTER JOIN correct_logic_listed c ON c.SECUCODE = w.SECUCODE
ORDER BY COALESCE(w.SECUCODE, c.SECUCODE);

-- ============================================================
-- 综合验证：检查国城矿业的完整筛选路径
-- ============================================================

SELECT 
    '国城矿业完整验证' AS verification_type,
    s.SECUCODE AS 代码,
    s.SECUABBR AS 名称,
    s.LISTEDDATE AS 上市日期,
    s.LISTEDSECTOR AS 板块,
    s.LISTEDSTATE AS 当前上市状态,
    -- 行业信息
    (SELECT e.FIRSTINDUSTRYNAME 
     FROM lc_exgindustry e 
     WHERE e.COMPANYCODE = s.COMPANYCODE 
       AND e.STANDARD = 38
       AND e.INFOPUBLDATE <= TIMESTAMP('2025-07-25 23:59:59')
       AND (e.CANCELDATE IS NULL OR e.CANCELDATE > TIMESTAMP('2025-07-25 23:59:59'))
     ORDER BY e.INFOPUBLDATE DESC 
     LIMIT 1) AS 申万一级行业,
    (SELECT e.SECONDINDUSTRYNAME 
     FROM lc_exgindustry e 
     WHERE e.COMPANYCODE = s.COMPANYCODE 
       AND e.STANDARD = 38
       AND e.INFOPUBLDATE <= TIMESTAMP('2025-07-25 23:59:59')
       AND (e.CANCELDATE IS NULL OR e.CANCELDATE > TIMESTAMP('2025-07-25 23:59:59'))
     ORDER BY e.INFOPUBLDATE DESC 
     LIMIT 1) AS 申万二级行业,
    -- ST状态
    (SELECT COUNT(*) 
     FROM cs_riskalert ra 
     WHERE ra.INNERCODE = s.INNERCODE
       AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
       AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= TIMESTAMP('2025-07-25 23:59:59')
       AND DATE(ra.IMPLEMENTDATE) <= '2025-07-25'
       AND (ra.REMOVEDATE IS NULL 
            OR DATE(ra.REMOVEDATE) > '2025-07-25'
            OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) > TIMESTAMP('2025-07-25 23:59:59'))
    ) AS 观察日ST记录数,
    -- 财务数据
    (SELECT m.ENDDATE 
     FROM lc_mainindexnew m 
     WHERE m.COMPANYCODE = s.COMPANYCODE
       AND m.ENDDATE <= TIMESTAMP('2025-07-25 23:59:59')
       AND m.INFOPUBLDATE <= TIMESTAMP('2025-07-25 23:59:59')
       AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD('2025-07-25', INTERVAL 1 DAY)
     ORDER BY m.ENDDATE DESC, m.INFOPUBLDATE DESC, m.JSID DESC
     LIMIT 1) AS 最新报告期,
    (SELECT m.ROETTM 
     FROM lc_mainindexnew m 
     WHERE m.COMPANYCODE = s.COMPANYCODE
       AND m.ENDDATE <= TIMESTAMP('2025-07-25 23:59:59')
       AND m.INFOPUBLDATE <= TIMESTAMP('2025-07-25 23:59:59')
       AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD('2025-07-25', INTERVAL 1 DAY)
     ORDER BY m.ENDDATE DESC, m.INFOPUBLDATE DESC, m.JSID DESC
     LIMIT 1) AS TTM_ROE,
    (SELECT m.OPERATINGREVENUEGROWRATE 
     FROM lc_mainindexnew m 
     WHERE m.COMPANYCODE = s.COMPANYCODE
       AND m.ENDDATE <= TIMESTAMP('2025-07-25 23:59:59')
       AND m.INFOPUBLDATE <= TIMESTAMP('2025-07-25 23:59:59')
       AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD('2025-07-25', INTERVAL 1 DAY)
     ORDER BY m.ENDDATE DESC, m.INFOPUBLDATE DESC, m.JSID DESC
     LIMIT 1) AS 营收同比增速,
    (SELECT m.NPPARENTCOMPANYYOY 
     FROM lc_mainindexnew m 
     WHERE m.COMPANYCODE = s.COMPANYCODE
       AND m.ENDDATE <= TIMESTAMP('2025-07-25 23:59:59')
       AND m.INFOPUBLDATE <= TIMESTAMP('2025-07-25 23:59:59')
       AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD('2025-07-25', INTERVAL 1 DAY)
     ORDER BY m.ENDDATE DESC, m.INFOPUBLDATE DESC, m.JSID DESC
     LIMIT 1) AS 归母净利润同比
FROM secumain s
WHERE s.SECUCODE = '000688';
