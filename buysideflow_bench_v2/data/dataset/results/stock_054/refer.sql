WITH
params AS (
    SELECT
        DATE('2025-04-30') AS as_of_date
),
target_index AS (
    SELECT s.INNERCODE
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE = '000852'
    ORDER BY s.INNERCODE
    LIMIT 1
),
index_constituents AS (
    SELECT DISTINCT
        ic.SECUINNERCODE AS innercode,
        sm.COMPANYCODE
    FROM lc_indexcomponent ic
    JOIN target_index ti
      ON ti.INNERCODE = ic.INDEXINNERCODE
    JOIN params p
      ON ic.INDATE <= p.as_of_date
    JOIN secumain sm
      ON sm.INNERCODE = ic.SECUINNERCODE
    WHERE (ic.OUTDATE IS NULL OR ic.OUTDATE > p.as_of_date)
      AND sm.SECUCATEGORY = 1
),
stock_info AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        s.LISTEDSECTOR,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS company
    FROM secumain s
    JOIN index_constituents ic
      ON ic.innercode = s.INNERCODE
    WHERE s.SECUCATEGORY = 1
),
industry_asof AS (
    SELECT
        COMPANYCODE,
        FIRSTINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_info si
          ON si.COMPANYCODE = e.COMPANYCODE
         AND si.LISTEDSECTOR <> 7
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(e.INSERTTIME, e.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
        UNION ALL
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
            ) AS rn
        FROM lc_stibexgindustry e
        JOIN stock_info si
          ON si.COMPANYCODE = e.COMPANYCODE
         AND si.LISTEDSECTOR = 7
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(e.INSERTTIME, e.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND e.EFFECTIVEDATE <= p.as_of_date
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    ) t
    WHERE rn = 1
),
candidate AS (
    SELECT
        si.INNERCODE,
        si.COMPANYCODE,
        si.SECUCODE,
        si.company
    FROM stock_info si
    JOIN industry_asof ia
      ON ia.COMPANYCODE = si.COMPANYCODE
    WHERE ia.FIRSTINDUSTRYNAME NOT IN ('银行', '非银金融')
),
annual_bs_key AS (
    SELECT
        b.COMPANYCODE,
        b.ENDDATE,
        MAX(b.JSID) AS max_jsid
    FROM lc_balancesheetall b
    JOIN candidate c
      ON c.COMPANYCODE = b.COMPANYCODE
    JOIN params p
      ON b.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
     AND COALESCE(b.INSERTTIME, b.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE b.ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
      AND b.IFMERGED = 1
      AND b.IFADJUSTED = 2
      AND b.ACCOUNTINGSTANDARDS = 1
      AND b.TOTALCURRENTASSETS IS NOT NULL
      AND b.TOTALCURRENTLIABILITY IS NOT NULL
    GROUP BY b.COMPANYCODE, b.ENDDATE
),
annual_bs AS (
    SELECT
        b.COMPANYCODE,
        YEAR(b.ENDDATE) AS report_year,
        b.TOTALCURRENTASSETS - b.TOTALCURRENTLIABILITY AS working_capital
    FROM lc_balancesheetall b
    JOIN annual_bs_key k
      ON k.COMPANYCODE = b.COMPANYCODE
     AND k.ENDDATE = b.ENDDATE
     AND k.max_jsid = b.JSID
),
annual_cf AS (
    SELECT
        COMPANYCODE,
        YEAR(ENDDATE) AS report_year,
        NETOPERATECASHFLOW
    FROM (
        SELECT
            m.COMPANYCODE,
            m.ENDDATE,
            m.NETOPERATECASHFLOW,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE, m.ENDDATE
                ORDER BY CASE m.MARK
                             WHEN 1 THEN 0 WHEN 8 THEN 1 WHEN 7 THEN 2
                             WHEN 6 THEN 3 WHEN 2 THEN 4 ELSE 9
                         END,
                         m.INFOPUBLDATE DESC,
                         COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                         m.JSID DESC
            ) AS rn
        FROM lc_maindatanew m
        JOIN candidate c
          ON c.COMPANYCODE = m.COMPANYCODE
        JOIN params p
          ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE m.MARK IN (1, 8, 7, 6, 2)
          AND m.ACCOUNTINGSTANDARDS = 1
          AND m.ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
          AND m.NETOPERATECASHFLOW IS NOT NULL
    ) t
    WHERE rn = 1
),
pivot_3y AS (
    SELECT
        bs.COMPANYCODE,
        MAX(CASE WHEN bs.report_year = 2024 THEN bs.working_capital END) AS wc_2024,
        MAX(CASE WHEN bs.report_year = 2023 THEN bs.working_capital END) AS wc_2023,
        MAX(CASE WHEN bs.report_year = 2022 THEN bs.working_capital END) AS wc_2022,
        MAX(CASE WHEN cf.report_year = 2024 THEN cf.NETOPERATECASHFLOW END) AS cfo_2024,
        MAX(CASE WHEN cf.report_year = 2023 THEN cf.NETOPERATECASHFLOW END) AS cfo_2023,
        MAX(CASE WHEN cf.report_year = 2022 THEN cf.NETOPERATECASHFLOW END) AS cfo_2022
    FROM annual_bs bs
    JOIN annual_cf cf
      ON cf.COMPANYCODE = bs.COMPANYCODE
     AND cf.report_year = bs.report_year
    GROUP BY bs.COMPANYCODE
),
watch_pool AS (
    SELECT *
    FROM pivot_3y
    WHERE wc_2024 < 0
      AND wc_2023 < 0
      AND wc_2022 < 0
      AND cfo_2024 > 0
      AND cfo_2023 > 0
      AND cfo_2022 > 0
),
layered AS (
    SELECT
        CASE
            WHEN ABS(wp.wc_2024) > ABS(wp.wc_2023)
             AND ABS(wp.wc_2023) > ABS(wp.wc_2022)
            THEN '强化型'
            WHEN ABS(wp.wc_2024) < ABS(wp.wc_2023)
             AND ABS(wp.wc_2023) < ABS(wp.wc_2022)
            THEN '收敛型'
            ELSE '波动型'
        END AS state_bucket
    FROM watch_pool wp
),
state_stats AS (
    SELECT
        state_bucket,
        COUNT(*) AS sample_count
    FROM layered
    GROUP BY state_bucket
),
base_count AS (
    SELECT COUNT(*) AS comparable_count
    FROM watch_pool
),
state_order AS (
    SELECT 1 AS sort_order, '强化型' AS state_bucket
    UNION ALL SELECT 2, '收敛型'
    UNION ALL SELECT 3, '波动型'
)
SELECT
    so.state_bucket AS `状态类型`,
    COALESCE(ss.sample_count, 0) AS `公司数`,
    ROUND(COALESCE(ss.sample_count, 0) / NULLIF(bc.comparable_count, 0) * 100, 2) AS `占筛选样本比例`
FROM state_order so
CROSS JOIN base_count bc
LEFT JOIN state_stats ss
  ON ss.state_bucket = so.state_bucket
ORDER BY so.sort_order;
