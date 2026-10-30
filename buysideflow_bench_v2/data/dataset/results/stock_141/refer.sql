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
        FROM (
            SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID, SECUMARKET
            FROM lc_liststatus
            UNION ALL
            SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID, 83 AS SECUMARKET
            FROM lc_stibliststatus
        ) ls
        JOIN params p
          ON ls.CHANGEDATE <= p.as_of_date
         AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
regular_stock_base AS (
    SELECT
        INNERCODE,
        COMPANYCODE
    FROM (
        SELECT
            sm.INNERCODE,
            sm.COMPANYCODE,
            ROW_NUMBER() OVER (
                PARTITION BY sm.COMPANYCODE
                ORDER BY
                    CASE WHEN sm.SECUMARKET = 83 THEN 1 ELSE 2 END,
                    CASE sm.LISTEDSECTOR
                        WHEN 1 THEN 1
                        WHEN 2 THEN 2
                        WHEN 6 THEN 3
                        ELSE 9
                    END,
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
          AND sm.LISTEDSECTOR <> 7
          AND sm.COMPANYCODE IS NOT NULL
    ) t
    WHERE rn = 1
),
stib_stock_base AS (
    SELECT
        INNERCODE,
        COMPANYCODE
    FROM (
        SELECT
            sm.INNERCODE,
            sm.COMPANYCODE,
            ROW_NUMBER() OVER (
                PARTITION BY sm.COMPANYCODE
                ORDER BY sm.SECUCODE
            ) AS rn
        FROM secumain sm
        JOIN params p
          ON sm.LISTEDDATE <= p.as_of_date
        JOIN latest_list_status lsa
          ON lsa.INNERCODE = sm.INNERCODE
         AND lsa.CHANGETYPE IN (1, 3)
        WHERE sm.SECUCATEGORY = 1
          AND sm.SECUMARKET = 83
          AND sm.LISTEDSECTOR = 7
          AND sm.COMPANYCODE IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert d
              WHERE d.INNERCODE = sm.INNERCODE
                AND d.RISKALERTTYPE = 9
                AND d.IMPLEMENTDATE <= p.as_of_date
                AND d.IMPLANNOUCEDATE <= p.as_of_date
          )
    ) t
    WHERE rn = 1
),
st_stocks AS (
    SELECT DISTINCT
        ra.INNERCODE
    FROM cs_riskalert ra
    JOIN params p
      ON DATE(COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE)) <= p.as_of_date
     AND DATE(ra.IMPLEMENTDATE) <= p.as_of_date
    WHERE ra.RISKALERTTYPE IN (1, 5, 12, 14)
      AND (
          ra.REMOVEDATE IS NULL
          OR DATE(ra.REMOVEDATE) > p.as_of_date
          OR DATE(COALESCE(ra.REMOVEINFOPUBLDATE, ra.REMOVEDATE)) > p.as_of_date
      )
),
regular_industry_ranked AS (
    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYNAME,
        ROW_NUMBER() OVER (
            PARTITION BY e.COMPANYCODE
            ORDER BY e.INFOPUBLDATE DESC,
                     COALESCE(e.CANCELDATE, DATE('9999-12-31')) DESC,
                     e.JSID DESC,
                     e.ID DESC,
                     e.FIRSTINDUSTRYNAME
        ) AS rn
    FROM lc_exgindustry e
    JOIN params p
      ON e.INFOPUBLDATE <= p.as_of_date
    WHERE e.STANDARD = 38
      AND e.FIRSTINDUSTRYNAME IS NOT NULL
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
),
regular_industry_asof AS (
    SELECT
        COMPANYCODE,
        FIRSTINDUSTRYNAME
    FROM regular_industry_ranked
    WHERE rn = 1
),
stib_industry_ranked AS (
    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYNAME,
        ROW_NUMBER() OVER (
            PARTITION BY e.COMPANYCODE
            ORDER BY e.EFFECTIVEDATE DESC,
                     e.INFOPUBLDATE DESC,
                     COALESCE(e.CANCELDATE, DATE('9999-12-31')) DESC,
                     e.JSID DESC,
                     e.ID DESC,
                     e.FIRSTINDUSTRYNAME
        ) AS rn
    FROM lc_stibexgindustry e
    JOIN params p
      ON e.INFOPUBLDATE <= p.as_of_date
    WHERE e.STANDARD = 38
      AND e.FIRSTINDUSTRYNAME IS NOT NULL
      AND e.EFFECTIVEDATE <= p.as_of_date
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
),
stib_industry_asof AS (
    SELECT
        COMPANYCODE,
        FIRSTINDUSTRYNAME
    FROM stib_industry_ranked
    WHERE rn = 1
),
regular_financial_ranked AS (
    SELECT
        mi.COMPANYCODE,
        mi.ENDDATE,
        mi.GROSSINCOMERATIO,
        ROW_NUMBER() OVER (
            PARTITION BY mi.COMPANYCODE
            ORDER BY mi.ENDDATE DESC,
                     mi.INFOPUBLDATE DESC,
                     mi.INSERTTIME DESC,
                     mi.JSID DESC,
                     mi.ID DESC
        ) AS rn
    FROM lc_mainindexnew mi
    JOIN params p
      ON mi.INFOPUBLDATE <= p.as_of_date
     AND mi.ENDDATE <= p.as_of_date
),
regular_latest_financial AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        GROSSINCOMERATIO
    FROM regular_financial_ranked
    WHERE rn = 1
),
stib_financial_ranked AS (
    SELECT
        mi.COMPANYCODE,
        mi.ENDDATE,
        mi.GROSSINCOMERATIO,
        mi.MAININCOMEPS,
        ROW_NUMBER() OVER (
            PARTITION BY mi.COMPANYCODE
            ORDER BY mi.ENDDATE DESC,
                     CASE WHEN mi.IFMERGED = 1 THEN 0 ELSE 1 END,
                     CASE WHEN mi.IFADJUSTED = 1 THEN 0 ELSE 1 END,
                     mi.INFOPUBLDATE DESC,
                     mi.INSERTTIME DESC,
                     mi.JSID DESC,
                     mi.ID DESC
        ) AS rn
    FROM lc_stibmainindex mi
    JOIN params p
      ON mi.INFOPUBLDATE <= p.as_of_date
     AND mi.ENDDATE <= p.as_of_date
),
stib_latest_financial AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        GROSSINCOMERATIO,
        MAININCOMEPS
    FROM stib_financial_ranked
    WHERE rn = 1
),
revenue_ranked AS (
    SELECT
        md.COMPANYCODE,
        md.ENDDATE,
        md.OPERATINGREENUE,
        ROW_NUMBER() OVER (
            PARTITION BY md.COMPANYCODE, md.ENDDATE
            ORDER BY CASE md.MARK
                         WHEN 1 THEN 1
                         WHEN 8 THEN 2
                         WHEN 7 THEN 3
                         WHEN 6 THEN 4
                         WHEN 2 THEN 5
                         ELSE 99
                     END,
                     md.INFOPUBLDATE DESC,
                     md.INSERTTIME DESC,
                     md.JSID DESC,
                     md.ID DESC
        ) AS rn
    FROM lc_maindatanew md
    JOIN params p
      ON md.INFOPUBLDATE <= p.as_of_date
     AND md.ENDDATE <= p.as_of_date
    WHERE md.ACCOUNTINGSTANDARDS = 1
),
latest_revenue AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        OPERATINGREENUE
    FROM revenue_ranked
    WHERE rn = 1
),
regular_sample AS (
    SELECT
        ia.FIRSTINDUSTRYNAME,
        lf.GROSSINCOMERATIO
    FROM regular_stock_base sb
    JOIN regular_industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    JOIN regular_latest_financial lf
      ON lf.COMPANYCODE = sb.COMPANYCODE
    JOIN latest_revenue lr
      ON lr.COMPANYCODE = lf.COMPANYCODE
     AND lr.ENDDATE = lf.ENDDATE
    WHERE NOT EXISTS (
          SELECT 1
          FROM st_stocks st
          WHERE st.INNERCODE = sb.INNERCODE
      )
      AND lr.OPERATINGREENUE > 0
      AND lf.GROSSINCOMERATIO IS NOT NULL
      AND ABS(lf.GROSSINCOMERATIO) <= 100
),
stib_sample AS (
    SELECT
        ia.FIRSTINDUSTRYNAME,
        lf.GROSSINCOMERATIO
    FROM stib_stock_base sb
    JOIN stib_industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    JOIN stib_latest_financial lf
      ON lf.COMPANYCODE = sb.COMPANYCODE
    WHERE NOT EXISTS (
          SELECT 1
          FROM st_stocks st
          WHERE st.INNERCODE = sb.INNERCODE
      )
      AND lf.MAININCOMEPS > 0
      AND lf.GROSSINCOMERATIO IS NOT NULL
      AND ABS(lf.GROSSINCOMERATIO) <= 100
)
SELECT
    FIRSTINDUSTRYNAME AS `申万一级行业名称`,
    GROSSINCOMERATIO AS `毛利率`
FROM regular_sample
UNION ALL
SELECT
    FIRSTINDUSTRYNAME AS `申万一级行业名称`,
    GROSSINCOMERATIO AS `毛利率`
FROM stib_sample
ORDER BY `申万一级行业名称`, `毛利率`;
