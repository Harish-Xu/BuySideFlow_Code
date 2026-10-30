WITH params AS (
    SELECT DATE('2025-12-31') AS as_of_date
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
         AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
        WHERE ls.SECUMARKET IN (83, 90, 18)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
stock_base AS (
    SELECT
        s.COMPANYCODE,
        s.SECUCODE AS security_code,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS company_name,
        s.LISTEDSECTOR
    FROM (
        SELECT
            s.*,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.INNERCODE
            ) AS rn
        FROM secumain s
        JOIN params p
          ON s.LISTEDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        LEFT JOIN listed_asof la
          ON la.INNERCODE = s.INNERCODE
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90, 18)
          AND s.LISTEDSECTOR IN (1, 2, 6, 7, 8)
          AND la.CHANGETYPE IN (1, 3)
    ) s
    WHERE s.rn = 1
),
available_years AS (
    SELECT report_year
    FROM (
        SELECT DISTINCT YEAR(m.ENDDATE) AS report_year
        FROM lc_mainindexnew m
        JOIN stock_base sb
          ON sb.COMPANYCODE = m.COMPANYCODE
         AND sb.LISTEDSECTOR <> 7
        JOIN params p
          ON m.INFOPUBLDATE <= p.as_of_date
        WHERE MONTH(m.ENDDATE) = 12
          AND m.ROE IS NOT NULL

        UNION

        SELECT DISTINCT YEAR(m.ENDDATE) AS report_year
        FROM lc_stibmainindex m
        JOIN stock_base sb
          ON sb.COMPANYCODE = m.COMPANYCODE
         AND sb.LISTEDSECTOR = 7
        JOIN params p
          ON m.INFOPUBLDATE <= p.as_of_date
        WHERE MONTH(m.ENDDATE) = 12
          AND m.IFMERGED = 1
          AND m.ROE IS NOT NULL
    ) y
    ORDER BY report_year DESC
    LIMIT 3
),
annual_roe_nonstib AS (
    SELECT
        sb.COMPANYCODE,
        sb.security_code,
        sb.company_name,
        YEAR(m.ENDDATE) AS report_year,
        m.ROE,
        ROW_NUMBER() OVER (
            PARTITION BY sb.COMPANYCODE, YEAR(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC, m.INSERTTIME DESC, m.JSID DESC
        ) AS rn
    FROM lc_mainindexnew m
    JOIN stock_base sb
      ON sb.COMPANYCODE = m.COMPANYCODE
     AND sb.LISTEDSECTOR <> 7
    JOIN available_years y
      ON y.report_year = YEAR(m.ENDDATE)
    JOIN params p
      ON m.INFOPUBLDATE <= p.as_of_date
    WHERE MONTH(m.ENDDATE) = 12
      AND m.ROE IS NOT NULL
),
annual_roe_stib AS (
    SELECT
        sb.COMPANYCODE,
        sb.security_code,
        sb.company_name,
        YEAR(m.ENDDATE) AS report_year,
        m.ROE,
        ROW_NUMBER() OVER (
            PARTITION BY sb.COMPANYCODE, YEAR(m.ENDDATE)
            ORDER BY CASE WHEN m.IFADJUSTED = 1 THEN 0 ELSE 1 END,
                     m.INFOPUBLDATE DESC,
                     m.INSERTTIME DESC,
                     m.JSID DESC
        ) AS rn
    FROM lc_stibmainindex m
    JOIN stock_base sb
      ON sb.COMPANYCODE = m.COMPANYCODE
     AND sb.LISTEDSECTOR = 7
    JOIN available_years y
      ON y.report_year = YEAR(m.ENDDATE)
    JOIN params p
      ON m.INFOPUBLDATE <= p.as_of_date
    WHERE MONTH(m.ENDDATE) = 12
      AND m.IFMERGED = 1
      AND m.ROE IS NOT NULL
),
annual_roe_data AS (
    SELECT COMPANYCODE, security_code, company_name, report_year, ROE
    FROM annual_roe_nonstib
    WHERE rn = 1

    UNION ALL

    SELECT COMPANYCODE, security_code, company_name, report_year, ROE
    FROM annual_roe_stib
    WHERE rn = 1
),
qualified_companies AS (
    SELECT
        COMPANYCODE,
        AVG(ROE) AS avg_roe,
        MIN(ROE) AS min_roe,
        COUNT(DISTINCT report_year) AS year_count,
        MIN(report_year) AS min_year,
        MAX(report_year) AS max_year
    FROM annual_roe_data
    GROUP BY COMPANYCODE
    HAVING COUNT(DISTINCT report_year) = 3
       AND MAX(report_year) - MIN(report_year) = 2
       AND MIN(ROE) >= 30
       AND AVG(ROE) >= 40
)
SELECT
    a.security_code,
    a.company_name,
    a.report_year,
    ROUND(a.ROE, 4) AS ROE
FROM annual_roe_data a
JOIN qualified_companies q
  ON q.COMPANYCODE = a.COMPANYCODE
ORDER BY a.security_code, a.report_year;
