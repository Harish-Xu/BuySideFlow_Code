WITH
params AS (
    SELECT DATE('2025-12-12') AS as_of_date
),
listed_asof AS (
    SELECT INNERCODE, CHANGETYPE
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY ls.CHANGEDATE DESC,
                         COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
                         ls.JSID DESC,
                         ls.ID DESC
            ) AS rn
        FROM (
            SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID, SECUMARKET
            FROM lc_liststatus
            UNION ALL
            SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID, 83 AS SECUMARKET
            FROM lc_stibliststatus
        ) ls
        JOIN params p
          ON ls.CHANGEDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE ls.SECUMARKET IN (83, 90, 18)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
stock_seed AS (
    SELECT s.COMPANYCODE, MIN(s.INNERCODE) AS INNERCODE
    FROM secumain s
    JOIN params p
      ON s.LISTEDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    LEFT JOIN listed_asof la
      ON la.INNERCODE = s.INNERCODE
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90, 18)
      AND la.CHANGETYPE IN (1, 2, 3, 6)
    GROUP BY s.COMPANYCODE
),
stock_base AS (
    SELECT s.INNERCODE, s.COMPANYCODE, s.SECUCODE, s.LISTEDSECTOR
    FROM stock_seed ss
    JOIN secumain s ON s.INNERCODE = ss.INNERCODE
),
industry_asof AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME AS industry
    FROM (
        SELECT e.COMPANYCODE, e.FIRSTINDUSTRYNAME,
               ROW_NUMBER() OVER (
                   PARTITION BY e.COMPANYCODE
                   ORDER BY e.INFOPUBLDATE DESC,
                            e.JSID DESC, e.ID DESC
               ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb ON sb.COMPANYCODE = e.COMPANYCODE AND sb.LISTEDSECTOR <> 7
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
        UNION ALL
        SELECT e.COMPANYCODE, e.FIRSTINDUSTRYNAME,
               ROW_NUMBER() OVER (
                   PARTITION BY e.COMPANYCODE
                   ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC, e.ID DESC
               ) AS rn
        FROM lc_stibexgindustry e
        JOIN stock_base sb ON sb.COMPANYCODE = e.COMPANYCODE AND sb.LISTEDSECTOR = 7
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND e.EFFECTIVEDATE <= p.as_of_date
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    ) t
    WHERE rn = 1
      AND FIRSTINDUSTRYNAME IN ('食品饮料', '医药生物', '家用电器')
),
candidate AS (
    SELECT sb.COMPANYCODE, sb.LISTEDSECTOR, ia.industry
    FROM stock_base sb
    JOIN industry_asof ia ON ia.COMPANYCODE = sb.COMPANYCODE
),
revenue_ranked AS (
    SELECT q.COMPANYCODE, q.ENDDATE, q.OPERATINGREVENUEYOY AS revenue_yoy,
           ROW_NUMBER() OVER (
               PARTITION BY q.COMPANYCODE, q.ENDDATE
               ORDER BY q.INFOPUBLDATE DESC,
                        CASE q.MARK WHEN 1 THEN 0 ELSE 1 END,
                        q.JSID DESC,
                        q.ID DESC
           ) AS rn
    FROM lc_qfinancialindexnew q
    JOIN candidate c ON c.COMPANYCODE = q.COMPANYCODE
    JOIN params p
      ON q.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE q.MARK IN (1, 2)
      AND q.ACCOUNTINGSTANDARDS = 1
      AND q.ENDDATE IN (
          DATE('2023-12-31'), DATE('2024-03-31'), DATE('2024-06-30'), DATE('2024-09-30'),
          DATE('2024-12-31'), DATE('2025-03-31'), DATE('2025-06-30'), DATE('2025-09-30')
      )
),
revenue_8q AS (
    SELECT COMPANYCODE, ENDDATE, revenue_yoy
    FROM revenue_ranked
    WHERE rn = 1
      AND revenue_yoy IS NOT NULL
),
series_8q AS (
    SELECT COMPANYCODE,
           AVG(revenue_yoy) AS avg_revenue_yoy,
           STDDEV_POP(revenue_yoy) AS std_revenue_yoy,
           COUNT(*) AS quarter_cnt
    FROM revenue_8q
    GROUP BY COMPANYCODE
),
scored AS (
    SELECT c.industry,
           s.std_revenue_yoy / NULLIF(s.avg_revenue_yoy, 0) AS cv
    FROM candidate c
    JOIN series_8q s ON s.COMPANYCODE = c.COMPANYCODE
    WHERE s.quarter_cnt = 8
      AND s.avg_revenue_yoy > 0
)
SELECT
    s.industry AS `行业`,
    CAST(SUM(CASE WHEN s.cv < 0.4 THEN 1 ELSE 0 END) AS UNSIGNED) AS `满足数`,
    COUNT(*) AS `有效样本数`,
    ROUND(SUM(CASE WHEN s.cv < 0.4 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0) * 100, 2) AS `占比`
FROM scored s
GROUP BY s.industry
ORDER BY `占比` DESC, `满足数` DESC, `行业`;
