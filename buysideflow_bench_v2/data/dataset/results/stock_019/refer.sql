WITH params AS (
    SELECT DATE('2025-12-31') AS observation_date
),
cb_pool AS (
    SELECT DISTINCT
        cb.INNERCODE AS bond_innercode,
        sm.COMPANYCODE
    FROM bond_conbdbasicinfo cb
    JOIN secumain sm
     ON sm.INNERCODE = cb.STOCKINNERCODE
     AND sm.SECUCATEGORY = 1
     AND sm.SECUMARKET IN (83, 90, 18)
    JOIN params p
      ON cb.STARTDATE <= p.observation_date
     AND cb.ENDDATE > p.observation_date
    WHERE cb.BONDFORM = 1
      AND cb.INITIALINFOPUBLDATE <= p.observation_date
      AND (cb.DELISTDATE IS NULL OR cb.DELISTDATE > p.observation_date)
      AND (cb.EARLYMATURITYDATE IS NULL OR cb.EARLYMATURITYDATE > p.observation_date)
),
industry_records AS (
    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYNAME,
        e.INFOPUBLDATE,
        e.ID,
        0 AS source_priority
    FROM lc_exgindustry e
    JOIN cb_pool c ON c.COMPANYCODE = e.COMPANYCODE
    JOIN params p
      ON e.INFOPUBLDATE <= p.observation_date
     AND COALESCE(e.INSERTTIME, e.INFOPUBLDATE) < DATE_ADD(p.observation_date, INTERVAL 1 DAY)
     AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.observation_date)
    WHERE e.STANDARD = 38
      AND e.FIRSTINDUSTRYNAME IS NOT NULL
    UNION ALL
    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYNAME,
        e.INFOPUBLDATE,
        e.ID,
        1 AS source_priority
    FROM lc_stibexgindustry e
    JOIN cb_pool c ON c.COMPANYCODE = e.COMPANYCODE
    JOIN params p
      ON e.EFFECTIVEDATE <= p.observation_date
     AND e.INFOPUBLDATE <= p.observation_date
     AND COALESCE(e.INSERTTIME, e.INFOPUBLDATE) < DATE_ADD(p.observation_date, INTERVAL 1 DAY)
     AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.observation_date)
    WHERE e.STANDARD = 38
      AND e.FIRSTINDUSTRYNAME IS NOT NULL
),
industry_asof AS (
    SELECT x.COMPANYCODE,
           x.FIRSTINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC, e.source_priority, e.ID DESC
            ) AS rn
        FROM industry_records e
    ) x
    WHERE x.rn = 1
),
classified AS (
    SELECT
        c.bond_innercode,
        c.COMPANYCODE,
        ia.FIRSTINDUSTRYNAME AS industry_name
    FROM cb_pool c
    LEFT JOIN industry_asof ia
      ON ia.COMPANYCODE = c.COMPANYCODE
),
summary_rows AS (
    SELECT
        'summary' AS result_type,
        '合计' AS industry_name,
        COUNT(DISTINCT bond_innercode) AS bond_count,
        COUNT(DISTINCT COMPANYCODE) AS company_count,
        0 AS sort_group,
        0 AS sort_order
    FROM classified
    UNION ALL
    SELECT
        'summary',
        '已归类',
        COUNT(DISTINCT bond_innercode),
        COUNT(DISTINCT COMPANYCODE),
        0,
        1
    FROM classified
    WHERE industry_name IS NOT NULL
    UNION ALL
    SELECT
        'summary',
        '未分类',
        COUNT(DISTINCT bond_innercode),
        COUNT(DISTINCT COMPANYCODE),
        0,
        2
    FROM classified
    WHERE industry_name IS NULL
),
industry_rows AS (
    SELECT
        'industry' AS result_type,
        industry_name,
        COUNT(DISTINCT bond_innercode) AS bond_count,
        COUNT(DISTINCT COMPANYCODE) AS company_count,
        1 AS sort_group,
        ROW_NUMBER() OVER (
            ORDER BY COUNT(DISTINCT bond_innercode) DESC, industry_name
        ) AS sort_order
    FROM classified
    WHERE industry_name IS NOT NULL
    GROUP BY industry_name
)
SELECT
    result_type,
    industry_name,
    bond_count,
    company_count
FROM (
    SELECT * FROM summary_rows
    UNION ALL
    SELECT * FROM industry_rows
) t
ORDER BY sort_group, sort_order;
