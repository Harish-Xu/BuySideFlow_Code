WITH
params AS (
    SELECT TIMESTAMP('2025-08-31 23:59:59') AS as_of_dt
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
          ON ls.CHANGEDATE <= p.as_of_dt
         AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME <= p.as_of_dt)
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
        stock_name
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUCODE,
            s.LISTEDSECTOR,
            COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.INNERCODE
            ) AS rn
        FROM secumain s
        LEFT JOIN listed_asof la
          ON la.INNERCODE = s.INNERCODE
        JOIN params p
          ON s.LISTEDDATE <= p.as_of_dt
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND la.CHANGETYPE IN (1, 3)
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              WHERE ra.INNERCODE = s.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= p.as_of_dt
                AND DATE(ra.IMPLEMENTDATE) <= DATE(p.as_of_dt)
                AND (
                    ra.REMOVEDATE IS NULL
                    OR DATE(ra.REMOVEDATE) > DATE(p.as_of_dt)
                    OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) > p.as_of_dt
                )
          )
    ) t
    WHERE rn = 1
),
industry_asof AS (
    SELECT
        COMPANYCODE,
        FIRSTINDUSTRYNAME,
        SECONDINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            e.SECONDINDUSTRYNAME,
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
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_dt)
        UNION ALL
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            e.SECONDINDUSTRYNAME,
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
        m.NPPARENTCOMPANYYOY,
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
    WHERE m.ENDDATE = DATE('2025-06-30')
),
stib_mainindex_ver AS (
    SELECT
        m.COMPANYCODE,
        m.ENDDATE,
        m.INFOPUBLDATE,
        m.ROETTM,
        m.OPERATINGREVENUEYOY AS OPERATINGREVENUEGROWRATE,
        m.NPPARENTCOMPANYYOY,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY m.INFOPUBLDATE DESC,
                     CASE WHEN m.IFADJUSTED = 1 THEN 0 ELSE 1 END, m.JSID DESC
        ) AS rn_ver
    FROM lc_stibmainindex m
    JOIN stock_base sb
      ON sb.COMPANYCODE = m.COMPANYCODE
     AND sb.LISTEDSECTOR = 7
    JOIN params p
      ON m.INFOPUBLDATE <= p.as_of_dt
    WHERE m.ENDDATE = DATE('2025-06-30')
      AND m.IFMERGED = 1
),
mainindex_all AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        ROETTM,
        OPERATINGREVENUEGROWRATE,
        NPPARENTCOMPANYYOY
    FROM nonstib_mainindex_ver
    WHERE rn_ver = 1
    UNION ALL
    SELECT
        COMPANYCODE,
        ENDDATE,
        ROETTM,
        OPERATINGREVENUEGROWRATE,
        NPPARENTCOMPANYYOY
    FROM stib_mainindex_ver
    WHERE rn_ver = 1
),
latest_mainindex AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        ROETTM,
        OPERATINGREVENUEGROWRATE,
        NPPARENTCOMPANYYOY
    FROM (
        SELECT
            COMPANYCODE,
            ENDDATE,
            ROETTM,
            OPERATINGREVENUEGROWRATE,
            NPPARENTCOMPANYYOY,
            ROW_NUMBER() OVER (
                PARTITION BY COMPANYCODE
                ORDER BY ENDDATE DESC
            ) AS rn_company
        FROM mainindex_all
    ) t
    WHERE rn_company = 1
)
SELECT
    sb.SECUCODE AS `证券代码`,
    sb.stock_name AS `证券简称`,
    ia.SECONDINDUSTRYNAME AS `申万二级行业`,
    ROUND(mi.OPERATINGREVENUEGROWRATE, 4) AS `营业收入同比增速(%)`,
    ROUND(mi.ROETTM, 4) AS `TTM ROE(%)`
FROM stock_base sb
JOIN industry_asof ia ON ia.COMPANYCODE = sb.COMPANYCODE
JOIN latest_mainindex mi ON mi.COMPANYCODE = sb.COMPANYCODE
WHERE
    ia.FIRSTINDUSTRYNAME IN ('基础化工', '有色金属')
    AND mi.ROETTM > 15
    AND mi.OPERATINGREVENUEGROWRATE > 30
    AND mi.NPPARENTCOMPANYYOY > 0
ORDER BY
    mi.ROETTM DESC, mi.OPERATINGREVENUEGROWRATE DESC, sb.SECUCODE ASC;
