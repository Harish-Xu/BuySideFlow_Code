WITH
params AS (
    SELECT TIMESTAMP('2026-02-12 23:59:59') AS as_of_dt
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
         AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) <= p.as_of_dt
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
            ROW_NUMBER() OVER (PARTITION BY s.COMPANYCODE ORDER BY s.INNERCODE) AS rn
        FROM secumain s
        LEFT JOIN listed_asof la
          ON la.INNERCODE = s.INNERCODE
        JOIN params p
          ON s.LISTEDDATE <= p.as_of_dt
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
qfin_ver AS (
    SELECT
        q.COMPANYCODE,
        q.ENDDATE,
        q.INFOPUBLDATE,
        q.INVENTORYTRATE,
        q.FIXEDASSETTRATE,
        q.OPERATINGREVENUEYOY,
        ROW_NUMBER() OVER (
            PARTITION BY q.COMPANYCODE, q.ENDDATE
            ORDER BY q.INFOPUBLDATE DESC,
                     q.JSID DESC
        ) AS rn_ver
    FROM lc_qfinancialindexnew q
    JOIN stock_base sb
      ON sb.COMPANYCODE = q.COMPANYCODE
    JOIN params p
      ON q.INFOPUBLDATE <= p.as_of_dt
    WHERE q.ENDDATE <= p.as_of_dt
      AND MONTH(q.ENDDATE) IN (3, 6, 9, 12)
      AND COALESCE(q.ACCOUNTINGSTANDARDS, 1) = 1
),
last_three AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        INVENTORYTRATE,
        FIXEDASSETTRATE,
        OPERATINGREVENUEYOY,
        ROW_NUMBER() OVER (
            PARTITION BY COMPANYCODE
            ORDER BY ENDDATE DESC
        ) AS rn_desc
    FROM qfin_ver
    WHERE rn_ver = 1
),
pivoted AS (
    SELECT
        COMPANYCODE,
        COUNT(*) AS q_cnt,
        MIN(ENDDATE) AS earliest_enddate,
        MAX(CASE WHEN rn_desc = 1 THEN ENDDATE END) AS latest_enddate,
        MAX(CASE WHEN rn_desc = 1 THEN INVENTORYTRATE END) AS latest_inventorytrate,
        MAX(CASE WHEN rn_desc = 1 THEN FIXEDASSETTRATE END) AS latest_fixedassettrate,
        MAX(CASE WHEN rn_desc = 1 THEN OPERATINGREVENUEYOY END) AS latest_revenue_yoy,
        MAX(CASE WHEN rn_desc = 2 THEN INVENTORYTRATE END) AS prev_inventorytrate,
        MAX(CASE WHEN rn_desc = 2 THEN FIXEDASSETTRATE END) AS prev_fixedassettrate,
        MAX(CASE WHEN rn_desc = 3 THEN INVENTORYTRATE END) AS prev2_inventorytrate,
        MAX(CASE WHEN rn_desc = 3 THEN FIXEDASSETTRATE END) AS prev2_fixedassettrate
    FROM last_three
    WHERE rn_desc <= 3
    GROUP BY COMPANYCODE
)
SELECT
    sb.SECUCODE AS `代码`,
    sb.stock_name AS `名称`,
    ia.SECONDINDUSTRYNAME AS `申万二级行业`,
    ROUND(p.latest_inventorytrate, 2) AS `最新单季存货周转率`,
    ROUND(p.latest_fixedassettrate, 2) AS `最新单季固定资产周转率`
FROM pivoted p
JOIN stock_base sb
  ON sb.COMPANYCODE = p.COMPANYCODE
JOIN industry_asof ia
  ON ia.COMPANYCODE = p.COMPANYCODE
WHERE ia.FIRSTINDUSTRYNAME IN ('有色金属', '基础化工', '钢铁', '煤炭')
  AND p.q_cnt = 3
  AND PERIOD_DIFF(EXTRACT(YEAR_MONTH FROM p.latest_enddate),
                  EXTRACT(YEAR_MONTH FROM p.earliest_enddate)) = 6
  AND p.latest_inventorytrate > p.prev_inventorytrate
  AND p.prev_inventorytrate > p.prev2_inventorytrate
  AND p.latest_fixedassettrate > p.prev_fixedassettrate
  AND p.prev_fixedassettrate > p.prev2_fixedassettrate
  AND p.latest_revenue_yoy > 0
ORDER BY ia.SECONDINDUSTRYNAME, sb.SECUCODE;
