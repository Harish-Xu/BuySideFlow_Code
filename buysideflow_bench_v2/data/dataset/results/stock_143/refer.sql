WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
latest_list_status AS (
    SELECT
        INNERCODE,
        CHANGETYPE
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY ls.CHANGEDATE DESC,
                         ls.INSERTTIME DESC,
                         ls.JSID DESC,
                         ls.ID DESC
            ) AS rn
        FROM lc_liststatus ls
        JOIN params p
          ON ls.CHANGEDATE <= p.as_of_date
         AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
mainboard_stocks AS (
    SELECT
        COMPANYCODE,
        SECUCODE,
        company_name
    FROM (
        SELECT
            sm.COMPANYCODE,
            sm.SECUCODE,
            COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, sm.SECUCODE) AS company_name,
            ROW_NUMBER() OVER (
                PARTITION BY sm.COMPANYCODE
                ORDER BY
                    CASE WHEN sm.SECUMARKET = 83 THEN 1 ELSE 2 END,
                    sm.SECUCODE
            ) AS rn
        FROM secumain sm
        JOIN params p
          ON sm.LISTEDDATE <= p.as_of_date
        JOIN latest_list_status lsa
          ON lsa.INNERCODE = sm.INNERCODE
         AND lsa.CHANGETYPE IN (1, 3)
        WHERE sm.SECUCATEGORY = 1
          AND sm.SECUMARKET IN (83, 90)
          AND sm.LISTEDSECTOR IN (1, 2)
          AND sm.COMPANYCODE IS NOT NULL
    ) t
    WHERE rn = 1
),
industry_ranked AS (
    SELECT
        e.COMPANYCODE,
        e.SECONDINDUSTRYNAME,
        ROW_NUMBER() OVER (
            PARTITION BY e.COMPANYCODE
            ORDER BY e.INFOPUBLDATE DESC,
                     COALESCE(e.CANCELDATE, DATE('9999-12-31')) DESC,
                     e.JSID DESC,
                     e.ID DESC,
                     e.SECONDINDUSTRYNAME
        ) AS rn
    FROM lc_exgindustry e
    JOIN params p
      ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE e.STANDARD = 38
      AND e.SECONDINDUSTRYNAME IS NOT NULL
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
),
liquor_companies AS (
    SELECT
        ms.COMPANYCODE,
        ms.SECUCODE,
        ms.company_name
    FROM mainboard_stocks ms
    JOIN industry_ranked ir
      ON ir.COMPANYCODE = ms.COMPANYCODE
     AND ir.rn = 1
     AND ir.SECONDINDUSTRYNAME = '白酒Ⅱ'
),
available_annual_years AS (
    SELECT DISTINCT
        YEAR(mi.ENDDATE) AS report_year
    FROM lc_mainindexnew mi
    JOIN liquor_companies lc
      ON lc.COMPANYCODE = mi.COMPANYCODE
    JOIN params p
      ON mi.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE MONTH(mi.ENDDATE) = 12
      AND mi.GROSSINCOMERATIO IS NOT NULL
    ORDER BY report_year DESC
    LIMIT 3
),
annual_ranked AS (
    SELECT
        mi.COMPANYCODE,
        YEAR(mi.ENDDATE) AS report_year,
        mi.GROSSINCOMERATIO,
        ROW_NUMBER() OVER (
            PARTITION BY mi.COMPANYCODE, YEAR(mi.ENDDATE)
            ORDER BY mi.INFOPUBLDATE DESC,
                     mi.INSERTTIME DESC,
                     mi.JSID DESC,
                     mi.ID DESC
        ) AS rn
    FROM lc_mainindexnew mi
    JOIN liquor_companies lc
      ON lc.COMPANYCODE = mi.COMPANYCODE
    JOIN available_annual_years y
      ON y.report_year = YEAR(mi.ENDDATE)
    JOIN params p
      ON mi.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE MONTH(mi.ENDDATE) = 12
      AND mi.GROSSINCOMERATIO IS NOT NULL
),
annual_latest AS (
    SELECT
        COMPANYCODE,
        report_year,
        GROSSINCOMERATIO
    FROM annual_ranked
    WHERE rn = 1
),
complete_three_years AS (
    SELECT
        COMPANYCODE
    FROM annual_latest
    GROUP BY COMPANYCODE
    HAVING COUNT(DISTINCT report_year) = 3
       AND MAX(report_year) - MIN(report_year) = 2
),
company_average AS (
    SELECT
        al.COMPANYCODE,
        AVG(al.GROSSINCOMERATIO) AS avg_gross_margin_3y
    FROM annual_latest al
    JOIN complete_three_years c
      ON c.COMPANYCODE = al.COMPANYCODE
    GROUP BY al.COMPANYCODE
),
industry_average AS (
    SELECT AVG(avg_gross_margin_3y) AS industry_avg_gross_margin_3y
    FROM company_average
),
qualified_companies AS (
    SELECT
        ca.COMPANYCODE,
        ca.avg_gross_margin_3y
    FROM company_average ca
    CROSS JOIN industry_average ia
    WHERE ca.avg_gross_margin_3y > ia.industry_avg_gross_margin_3y
),
latest_report_ranked AS (
    SELECT
        qc.COMPANYCODE,
        mi.GROSSINCOMERATIO AS latest_gross_margin,
        mi.ENDDATE AS report_date,
        ROW_NUMBER() OVER (
            PARTITION BY qc.COMPANYCODE
            ORDER BY mi.ENDDATE DESC,
                     mi.INFOPUBLDATE DESC,
                     mi.INSERTTIME DESC,
                     mi.JSID DESC,
                     mi.ID DESC
        ) AS rn
    FROM qualified_companies qc
    JOIN lc_mainindexnew mi
      ON mi.COMPANYCODE = qc.COMPANYCODE
    JOIN params p
      ON mi.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
     AND mi.ENDDATE <= p.as_of_date
    WHERE mi.GROSSINCOMERATIO IS NOT NULL
),
ranked_companies AS (
    SELECT
        lc.company_name,
        lc.SECUCODE,
        lr.latest_gross_margin,
        lr.report_date,
        ROW_NUMBER() OVER (
            ORDER BY lr.latest_gross_margin DESC, lc.SECUCODE ASC
        ) AS rank_num
    FROM latest_report_ranked lr
    JOIN liquor_companies lc
      ON lc.COMPANYCODE = lr.COMPANYCODE
    WHERE lr.rn = 1
)
SELECT
    company_name AS `公司简称`,
    SECUCODE AS `证券代码`,
    ROUND(latest_gross_margin, 4) AS `最新毛利率`,
    DATE_FORMAT(report_date, '%Y-%m-%d') AS `报告期`,
    rank_num AS `排名`
FROM ranked_companies
WHERE rank_num <= 3
ORDER BY rank_num;
