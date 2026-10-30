WITH params AS (
    SELECT DATE('2026-01-05') AS as_of_date
),
target_index AS (
    SELECT s.INNERCODE AS index_innercode,
           s.SECUCODE AS index_code,
           COALESCE(s.CHINAMEABBR, s.SECUABBR, s.CHINAME, s.SECUCODE) AS index_name
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE IN ('000922', '930955', '932365')
),
latest_weight_date AS (
    SELECT ti.index_innercode,
           MAX(w.ENDDATE) AS weight_date
    FROM target_index ti
    JOIN lc_indexcomponentsweight w
      ON w.INDEXCODE = ti.index_innercode
    JOIN params p
      ON w.ENDDATE <= p.as_of_date
    GROUP BY ti.index_innercode
),
component_weight AS (
    SELECT ti.index_code,
           ti.index_name,
           w.INNERCODE AS stock_innercode,
           w.WEIGHT
    FROM target_index ti
    JOIN latest_weight_date d
      ON d.index_innercode = ti.index_innercode
    JOIN lc_indexcomponentsweight w
      ON w.INDEXCODE = d.index_innercode
     AND w.ENDDATE = d.weight_date
    WHERE w.WEIGHT IS NOT NULL
),
stock_company AS (
    SELECT DISTINCT
           cw.index_code,
           cw.index_name,
           cw.stock_innercode,
           cw.WEIGHT,
           sm.COMPANYCODE
    FROM component_weight cw
    JOIN secumain sm
      ON sm.INNERCODE = cw.stock_innercode
    WHERE sm.COMPANYCODE IS NOT NULL
),
industry_candidates AS (
    SELECT
        sc.index_code,
        sc.stock_innercode,
        e.FIRSTINDUSTRYCODE AS industry_code,
        e.FIRSTINDUSTRYNAME AS industry_name,
        e.INFOPUBLDATE AS effective_date,
        e.INFOPUBLDATE AS available_date,
        e.INSERTTIME AS inserted_at,
        1 AS source_rank,
        e.ID AS record_id
    FROM stock_company sc
    JOIN lc_exgindustry e
      ON e.COMPANYCODE = sc.COMPANYCODE
     AND e.STANDARD = 38
    JOIN params p
      ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
     AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
     AND (e.INSERTTIME IS NULL OR e.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))

    UNION ALL

    SELECT
        sc.index_code,
        sc.stock_innercode,
        e.FIRSTINDUSTRYCODE AS industry_code,
        e.FIRSTINDUSTRYNAME AS industry_name,
        e.EFFECTIVEDATE AS effective_date,
        e.INFOPUBLDATE AS available_date,
        e.INSERTTIME AS inserted_at,
        2 AS source_rank,
        e.ID AS record_id
    FROM stock_company sc
    JOIN lc_stibexgindustry e
      ON e.COMPANYCODE = sc.COMPANYCODE
     AND e.STANDARD = 38
    JOIN params p
      ON e.EFFECTIVEDATE <= p.as_of_date
     AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
     AND e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
     AND e.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
),
industry_ranked AS (
    SELECT
        index_code,
        stock_innercode,
        industry_code,
        industry_name,
        ROW_NUMBER() OVER (
            PARTITION BY index_code, stock_innercode
            ORDER BY source_rank, effective_date DESC, available_date DESC, inserted_at DESC, record_id DESC
        ) AS rn
    FROM industry_candidates
),
industry_mapped AS (
    SELECT
        sc.index_code,
        sc.index_name,
        sc.WEIGHT,
        ei.industry_code,
        ei.industry_name
    FROM stock_company sc
    LEFT JOIN (
        SELECT index_code, stock_innercode, industry_code, industry_name
        FROM industry_ranked
        WHERE rn = 1
    ) ei
      ON ei.index_code = sc.index_code
     AND ei.stock_innercode = sc.stock_innercode
),
industry_weight AS (
    SELECT
        index_code,
        index_name,
        industry_code,
        industry_name,
        SUM(WEIGHT) AS industry_weight_pct
    FROM industry_mapped
    WHERE industry_code IS NOT NULL
      AND industry_name IS NOT NULL
    GROUP BY index_code, index_name, industry_code, industry_name
),
ranked AS (
    SELECT
        iw.*,
        ROW_NUMBER() OVER (
            PARTITION BY iw.index_code
            ORDER BY iw.industry_weight_pct DESC, iw.industry_code
        ) AS rn
    FROM industry_weight iw
)
SELECT
    industry_code AS `行业代码`,
    industry_name AS `行业名称`,
    index_code AS `指数代码`,
    index_name AS `指数名称`,
    ROUND(industry_weight_pct, 4) AS `行业占比`
FROM ranked
WHERE rn <= 3
ORDER BY index_code, industry_weight_pct DESC, industry_code;
