WITH
params AS (
    -- Morning meeting: disclosures dated November 3 are not yet available.
    SELECT DATE('2025-11-03') AS as_of_date
),
securities AS (
    SELECT s.INNERCODE, s.COMPANYCODE, s.SECUCODE, s.SECUABBR, s.LISTEDSECTOR
    FROM secumain s
    CROSS JOIN params p
    WHERE s.SECUMARKET IN (83, 90)
      AND s.SECUCATEGORY = 1
      AND s.LISTEDDATE <= p.as_of_date
),
listing_ranked AS (
    SELECT l.INNERCODE, l.CHANGETYPE,
           ROW_NUMBER() OVER (
               PARTITION BY l.INNERCODE
               ORDER BY l.CHANGEDATE DESC, l.INSERTTIME DESC, l.JSID DESC, l.ID DESC
           ) AS rn
    FROM (
        SELECT INNERCODE, CHANGETYPE, CHANGEDATE, INSERTTIME, JSID, ID
        FROM lc_liststatus
        UNION ALL
        SELECT INNERCODE, CHANGETYPE, CHANGEDATE, INSERTTIME, JSID, ID
        FROM lc_stibliststatus
    ) l
    JOIN securities s ON s.INNERCODE = l.INNERCODE
    CROSS JOIN params p
    WHERE l.CHANGEDATE <= p.as_of_date
      AND COALESCE(l.INSERTTIME, l.CHANGEDATE) < p.as_of_date
      AND l.CHANGETYPE BETWEEN 1 AND 9
),
stock_base AS (
    SELECT INNERCODE, COMPANYCODE, SECUCODE, SECUABBR, LISTEDSECTOR
    FROM (
        SELECT s.*,
               ROW_NUMBER() OVER (PARTITION BY s.COMPANYCODE ORDER BY s.SECUCODE) AS rn
        FROM securities s
        JOIN listing_ranked l ON l.INNERCODE = s.INNERCODE AND l.rn = 1
        -- Listing suspension/consolidation is not termination of listing.
        WHERE l.CHANGETYPE IN (1, 2, 3, 6)
    ) t
    WHERE rn = 1
),
industry_versions AS (
    SELECT e.COMPANYCODE, e.FIRSTINDUSTRYNAME, e.INFOPUBLDATE, e.JSID, e.ID
    FROM lc_exgindustry e
    JOIN stock_base s ON s.COMPANYCODE = e.COMPANYCODE AND s.LISTEDSECTOR <> 7
    CROSS JOIN params p
    WHERE e.STANDARD = 38
      AND e.INFOPUBLDATE < p.as_of_date
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    UNION ALL
    SELECT e.COMPANYCODE, e.FIRSTINDUSTRYNAME, e.INFOPUBLDATE, e.JSID, e.ID
    FROM lc_stibexgindustry e
    JOIN stock_base s ON s.COMPANYCODE = e.COMPANYCODE AND s.LISTEDSECTOR = 7
    CROSS JOIN params p
    WHERE e.STANDARD = 38
      AND e.INFOPUBLDATE < p.as_of_date
      AND e.EFFECTIVEDATE <= p.as_of_date
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
),
industry_ranked AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME,
           ROW_NUMBER() OVER (
               PARTITION BY COMPANYCODE ORDER BY INFOPUBLDATE DESC, JSID DESC, ID DESC
           ) AS rn
    FROM industry_versions
),
electronics AS (
    SELECT s.*
    FROM stock_base s
    JOIN industry_ranked i ON i.COMPANYCODE = s.COMPANYCODE AND i.rn = 1
    WHERE i.FIRSTINDUSTRYNAME = '电子'
),
financial_versions AS (
    SELECT m.COMPANYCODE, m.ENDDATE, m.ROETTM, m.INFOPUBLDATE, m.JSID, m.ID,
           0 AS adjustment_priority
    FROM lc_mainindexnew m
    JOIN electronics s ON s.COMPANYCODE = m.COMPANYCODE AND s.LISTEDSECTOR <> 7
    CROSS JOIN params p
    WHERE m.ENDDATE IN (DATE('2025-06-30'), DATE('2025-09-30'))
      AND m.INFOPUBLDATE < p.as_of_date
    UNION ALL
    SELECT m.COMPANYCODE, m.ENDDATE, m.ROETTM, m.INFOPUBLDATE, m.JSID, m.ID,
           CASE WHEN m.IFADJUSTED = 1 THEN 0 ELSE 1 END AS adjustment_priority
    FROM lc_stibmainindex m
    JOIN electronics s ON s.COMPANYCODE = m.COMPANYCODE AND s.LISTEDSECTOR = 7
    CROSS JOIN params p
    WHERE m.ENDDATE IN (DATE('2025-06-30'), DATE('2025-09-30'))
      AND m.INFOPUBLDATE < p.as_of_date
      AND m.IFMERGED = 1
),
financial_ranked AS (
    SELECT COMPANYCODE, ENDDATE, ROETTM,
           ROW_NUMBER() OVER (
               PARTITION BY COMPANYCODE, ENDDATE
               ORDER BY INFOPUBLDATE DESC, adjustment_priority, JSID DESC, ID DESC
           ) AS rn
    FROM financial_versions
),
top_ten AS (
    SELECT s.COMPANYCODE, s.SECUCODE, s.SECUABBR, f.ROETTM
    FROM electronics s
    JOIN financial_ranked f ON f.COMPANYCODE = s.COMPANYCODE
      AND f.ENDDATE = DATE('2025-09-30') AND f.rn = 1
    WHERE f.ROETTM IS NOT NULL
    ORDER BY f.ROETTM DESC, s.SECUCODE ASC
    LIMIT 10
)
SELECT t.SECUCODE AS `股票代码`,
       t.SECUABBR AS `简称`,
       h.ROETTM AS `2025年半年报TTM ROE(%)`,
       t.ROETTM AS `2025年三季报TTM ROE(%)`
FROM top_ten t
LEFT JOIN financial_ranked h ON h.COMPANYCODE = t.COMPANYCODE
  AND h.ENDDATE = DATE('2025-06-30') AND h.rn = 1
ORDER BY t.ROETTM DESC, t.SECUCODE ASC;
