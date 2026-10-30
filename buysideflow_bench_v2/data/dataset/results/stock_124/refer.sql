WITH
params AS (
    SELECT
        DATE('2025-07-01') AS quarter_start,
        DATE('2025-09-30') AS quarter_end
),
target_index AS (
    SELECT INNERCODE AS index_innercode
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '000300'
    ORDER BY INNERCODE
    LIMIT 1
),
completed_reduction_plans AS (
    SELECT
        tp.ID AS plan_id,
        tp.COMPANYCODE AS company_code,
        DATE(tp.INFOPUBLDATE) AS completion_announcement_date
    FROM lc_transferplan tp
    JOIN params p
      ON DATE(tp.INFOPUBLDATE) BETWEEN p.quarter_start AND p.quarter_end
    WHERE tp.TRANSFERPLANTYPE IN (127, 128)
      AND tp.EVENTPROCEDURE = 2
      AND tp.COMPANYCODE IS NOT NULL
),
hs300_plans AS (
    SELECT cp.*
    FROM completed_reduction_plans cp
    WHERE EXISTS (
        SELECT 1
        FROM secumain sm
        JOIN lc_indexcomponent ic
          ON ic.SECUINNERCODE = sm.INNERCODE
        JOIN target_index ti
          ON ti.index_innercode = ic.INDEXINNERCODE
        WHERE sm.COMPANYCODE = cp.company_code
          AND sm.SECUCATEGORY = 1
          AND sm.SECUMARKET IN (83, 90)
          AND DATE(ic.INDATE) <= cp.completion_announcement_date
          AND (
              ic.OUTDATE IS NULL
              OR DATE(ic.OUTDATE) > cp.completion_announcement_date
          )
    )
),
industry_candidates AS (
    SELECT
        src.plan_id,
        src.industry_name,
        ROW_NUMBER() OVER (
            PARTITION BY src.plan_id
            ORDER BY
                src.valid_from DESC,
                src.publish_date DESC,
                src.source_priority,
                src.jsid DESC
        ) AS rn
    FROM (
        SELECT
            hp.plan_id,
            e.FIRSTINDUSTRYNAME AS industry_name,
            DATE(e.INFOPUBLDATE) AS valid_from,
            DATE(e.INFOPUBLDATE) AS publish_date,
            1 AS source_priority,
            e.JSID AS jsid
        FROM hs300_plans hp
        JOIN lc_exgindustry e
          ON e.COMPANYCODE = hp.company_code
         AND e.STANDARD = 38
         AND e.FIRSTINDUSTRYNAME IS NOT NULL
         AND DATE(e.INFOPUBLDATE) <= hp.completion_announcement_date
         AND (
             e.CANCELDATE IS NULL
             OR DATE(e.CANCELDATE) > hp.completion_announcement_date
         )

        UNION ALL

        SELECT
            hp.plan_id,
            e.FIRSTINDUSTRYNAME AS industry_name,
            DATE(e.EFFECTIVEDATE) AS valid_from,
            DATE(e.INFOPUBLDATE) AS publish_date,
            2 AS source_priority,
            e.JSID AS jsid
        FROM hs300_plans hp
        JOIN lc_stibexgindustry e
          ON e.COMPANYCODE = hp.company_code
         AND e.STANDARD = 38
         AND e.FIRSTINDUSTRYNAME IS NOT NULL
         AND DATE(e.EFFECTIVEDATE) <= hp.completion_announcement_date
         AND DATE(e.INFOPUBLDATE) <= hp.completion_announcement_date
         AND (
             e.CANCELDATE IS NULL
             OR DATE(e.CANCELDATE) > hp.completion_announcement_date
         )
    ) src
),
plan_industry AS (
    SELECT
        hp.plan_id,
        hp.company_code,
        COALESCE(ic.industry_name, '未知') AS industry_name
    FROM hs300_plans hp
    LEFT JOIN industry_candidates ic
      ON ic.plan_id = hp.plan_id
     AND ic.rn = 1
)
SELECT
    industry_name AS `申万一级行业`,
    COUNT(DISTINCT company_code) AS `涉及公司数`,
    COUNT(DISTINCT plan_id) AS `已完成减持计划数`
FROM plan_industry
GROUP BY industry_name
ORDER BY `已完成减持计划数` DESC, `申万一级行业` ASC;
