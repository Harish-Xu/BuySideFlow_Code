WITH
params AS (
    SELECT DATE('2025-08-28') AS as_of_dt
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
          ON ls.CHANGEDATE < DATE_ADD(p.as_of_dt, INTERVAL 1 DAY)
         AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) < DATE_ADD(p.as_of_dt, INTERVAL 1 DAY)
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
stock_base AS (
    SELECT
        INNERCODE,
        COMPANYCODE,
        SECUCODE,
        LISTEDSECTOR,
        LISTEDDATE,
        stock_name
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUCODE,
            s.LISTEDSECTOR,
            s.LISTEDDATE,
            COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.INNERCODE
            ) AS rn
        FROM secumain s
        LEFT JOIN listed_asof la
          ON la.INNERCODE = s.INNERCODE
        JOIN params p
          ON s.LISTEDDATE < DATE_ADD(p.as_of_dt, INTERVAL 1 DAY)
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2, 6, 7)
          AND la.CHANGETYPE IN (1, 3)
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              WHERE ra.INNERCODE = s.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= p.as_of_dt
                AND DATE(ra.IMPLEMENTDATE) <= p.as_of_dt
                AND (
                    ra.REMOVEDATE IS NULL
                    OR DATE(ra.REMOVEDATE) > p.as_of_dt
                    OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) > p.as_of_dt
                )
          )
    ) t
    WHERE rn = 1
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
                ORDER BY e.INFOPUBLDATE DESC,
                         e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR <> 7
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_dt
         AND COALESCE(e.INSERTTIME, e.INFOPUBLDATE) < DATE_ADD(p.as_of_dt, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_dt)
        UNION ALL
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC,
                         e.JSID DESC
            ) AS rn
        FROM lc_stibexgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR = 7
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_dt
         AND COALESCE(e.INSERTTIME, e.INFOPUBLDATE) < DATE_ADD(p.as_of_dt, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND e.EFFECTIVEDATE <= p.as_of_dt
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_dt)
    ) t
    WHERE rn = 1
),
nonstib_mainindex_ver AS (
    SELECT
        m.COMPANYCODE,
        m.ENDDATE,
        m.INFOPUBLDATE,
        m.ROETTM,
        m.OPERATINGREVENUEGROWRATE,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn_ver
    FROM lc_mainindexnew m
    JOIN stock_base sb
      ON sb.COMPANYCODE = m.COMPANYCODE
     AND sb.LISTEDSECTOR <> 7
    JOIN params p
      ON m.INFOPUBLDATE <= p.as_of_dt
     AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD(DATE(p.as_of_dt), INTERVAL 1 DAY)
    WHERE m.ENDDATE <= p.as_of_dt
),
stib_mainindex_ver AS (
    SELECT
        m.COMPANYCODE,
        m.ENDDATE,
        m.INFOPUBLDATE,
        m.ROETTM,
        m.OPERATINGREVENUEYOY AS OPERATINGREVENUEGROWRATE,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY CASE WHEN m.IFADJUSTED = 1 THEN 0 ELSE 1 END,
                     m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn_ver
    FROM lc_stibmainindex m
    JOIN stock_base sb
      ON sb.COMPANYCODE = m.COMPANYCODE
     AND sb.LISTEDSECTOR = 7
    JOIN params p
      ON m.INFOPUBLDATE <= p.as_of_dt
     AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD(DATE(p.as_of_dt), INTERVAL 1 DAY)
    WHERE m.ENDDATE <= p.as_of_dt
      AND m.IFMERGED = 1
),
mainindex_all AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        ROETTM,
        OPERATINGREVENUEGROWRATE
    FROM nonstib_mainindex_ver
    WHERE rn_ver = 1
    UNION ALL
    SELECT
        COMPANYCODE,
        ENDDATE,
        ROETTM,
        OPERATINGREVENUEGROWRATE
    FROM stib_mainindex_ver
    WHERE rn_ver = 1
),
latest_mainindex AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        ROETTM,
        OPERATINGREVENUEGROWRATE
    FROM (
        SELECT
            COMPANYCODE,
            ENDDATE,
            ROETTM,
            OPERATINGREVENUEGROWRATE,
            ROW_NUMBER() OVER (
                PARTITION BY COMPANYCODE
                ORDER BY ENDDATE DESC
            ) AS rn_company
        FROM mainindex_all
    ) t
    WHERE rn_company = 1
),
fd_ver AS (
    SELECT
        f.COMPANYCODE,
        f.ENDDATE,
        f.INFOPUBLDATE,
        RANDDEXPENSERATE,
        ROW_NUMBER() OVER (
            PARTITION BY f.COMPANYCODE, f.ENDDATE
            ORDER BY CASE f.MARK
                         WHEN 1 THEN 0
                         WHEN 8 THEN 1
                         WHEN 7 THEN 2
                         WHEN 6 THEN 3
                         WHEN 2 THEN 4
                         ELSE 9
                     END,
                     f.INFOPUBLDATE DESC, f.JSID DESC
        ) AS rn_ver
    FROM fin_derivative f
    JOIN params p
      ON f.INFOPUBLDATE <= p.as_of_dt
     AND COALESCE(f.INSERTTIME, f.INFOPUBLDATE) < DATE_ADD(DATE(p.as_of_dt), INTERVAL 1 DAY)
    WHERE f.ENDDATE <= p.as_of_dt
      AND MONTH(f.ENDDATE) = 12
      AND f.MARK IN (1, 8, 7, 6, 2)
),
latest_fd_annual AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        RANDDEXPENSERATE
    FROM (
        SELECT
            COMPANYCODE,
            ENDDATE,
            RANDDEXPENSERATE,
            ROW_NUMBER() OVER (
                PARTITION BY COMPANYCODE
                ORDER BY ENDDATE DESC
            ) AS rn_company
        FROM fd_ver
        WHERE rn_ver = 1
    ) t
    WHERE rn_company = 1
)
SELECT
    sb.SECUCODE AS `代码`,
    sb.stock_name AS `名称`,
    ROUND(fd.RANDDEXPENSERATE, 4) AS `研发强度`,
    ROUND(mi.OPERATINGREVENUEGROWRATE, 4) AS `营收同比增速`,
    ROUND(mi.ROETTM, 4) AS `TTM ROE`
FROM stock_base sb
JOIN industry_asof ia ON ia.COMPANYCODE = sb.COMPANYCODE
JOIN latest_mainindex mi ON mi.COMPANYCODE = sb.COMPANYCODE
JOIN latest_fd_annual fd ON fd.COMPANYCODE = sb.COMPANYCODE
WHERE
    ia.FIRSTINDUSTRYNAME IN ('医药生物')
  AND mi.ROETTM > 10
  AND mi.OPERATINGREVENUEGROWRATE > 20
  AND fd.RANDDEXPENSERATE > 15
ORDER BY
    fd.RANDDEXPENSERATE DESC, mi.ROETTM DESC, sb.SECUCODE ASC;
