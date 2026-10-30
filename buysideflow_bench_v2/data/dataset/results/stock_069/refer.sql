WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
hs300 AS (
    SELECT ibi.INDEXCODE AS index_code
    FROM lc_indexbasicinfo ibi
    INNER JOIN secumain sm ON sm.INNERCODE = ibi.INDEXCODE
    WHERE sm.SECUCATEGORY = 4
      AND sm.SECUCODE = '000300'
),
snapshot_members AS (
    SELECT DISTINCT
        DATE(w.ENDDATE) AS adjust_date,
        sm.COMPANYCODE,
        sm.LISTEDSECTOR
    FROM lc_indexcomponentsweight w
    INNER JOIN hs300 h ON w.INDEXCODE = h.index_code
    INNER JOIN secumain sm ON sm.INNERCODE = w.INNERCODE
    INNER JOIN params p
       ON DATE(w.ENDDATE) BETWEEN DATE('2025-01-01') AND p.as_of_date
),
industry_sw_latest AS (
    SELECT *
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME AS industry_name,
            sm.adjust_date,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE, sm.adjust_date
                ORDER BY
                    e.INFOPUBLDATE DESC,
                    e.JSID DESC
            ) AS rn
        FROM snapshot_members sm
        INNER JOIN lc_exgindustry e
           ON e.COMPANYCODE = sm.COMPANYCODE
           AND e.INFOPUBLDATE < DATE_ADD(sm.adjust_date, INTERVAL 1 DAY)
        WHERE sm.LISTEDSECTOR <> 7
          AND e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR DATE(e.CANCELDATE) > sm.adjust_date)
          AND e.FIRSTINDUSTRYNAME IS NOT NULL
        UNION ALL
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME AS industry_name,
            sm.adjust_date,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE, sm.adjust_date
                ORDER BY
                    e.EFFECTIVEDATE DESC,
                    e.INFOPUBLDATE DESC,
                    e.JSID DESC
            ) AS rn
        FROM snapshot_members sm
        INNER JOIN lc_stibexgindustry e
            ON e.COMPANYCODE = sm.COMPANYCODE
           AND e.EFFECTIVEDATE <= sm.adjust_date
           AND e.INFOPUBLDATE < DATE_ADD(sm.adjust_date, INTERVAL 1 DAY)
        WHERE sm.LISTEDSECTOR = 7
          AND e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR DATE(e.CANCELDATE) > sm.adjust_date)
          AND e.FIRSTINDUSTRYNAME IS NOT NULL
    ) t
    WHERE rn = 1
),
weight_industry AS (
    SELECT
        DATE(w.ENDDATE)                           AS adjust_date,
        COALESCE(ind.industry_name, '未知行业')    AS industry_name,
        SUM(w.WEIGHT)                            AS total_weight
    FROM lc_indexcomponentsweight w
    INNER JOIN hs300 h ON w.INDEXCODE = h.index_code
    INNER JOIN secumain sm ON w.INNERCODE = sm.INNERCODE
    INNER JOIN params p
       ON DATE(w.ENDDATE) BETWEEN DATE('2025-01-01') AND p.as_of_date
    LEFT JOIN industry_sw_latest ind
        ON sm.COMPANYCODE = ind.COMPANYCODE
       AND ind.adjust_date = DATE(w.ENDDATE)
    GROUP BY DATE(w.ENDDATE), ind.industry_name
),
ranked AS (
    SELECT *
    FROM (
        SELECT
            adjust_date,
            industry_name,
            total_weight,
            ROW_NUMBER() OVER (
                PARTITION BY adjust_date
                ORDER BY total_weight DESC, industry_name ASC
            ) AS rn
        FROM weight_industry
    ) t
    WHERE rn <= 3
)
SELECT
    DATE_FORMAT(adjust_date, '%Y-%m-%d') AS `权重日期`,
    industry_name                        AS `行业名称`,
    ROUND(total_weight, 4)               AS `行业占比`
FROM ranked
ORDER BY adjust_date, rn;
