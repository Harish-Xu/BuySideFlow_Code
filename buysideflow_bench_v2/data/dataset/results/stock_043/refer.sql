WITH
params AS (
    SELECT TIMESTAMP('2025-12-31 23:59:59') AS as_of_dt
),
asof_trade AS (
    SELECT MAX(v.TRADINGDAY) AS trade_day
    FROM lc_dindicesforvaluation v
    JOIN params p
      ON v.TRADINGDAY < DATE(p.as_of_dt)
),
stock_base AS (
    SELECT
        INNERCODE,
        COMPANYCODE,
        SECUCODE,
        stock_name
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUCODE,
            COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name,
            ROW_NUMBER() OVER (PARTITION BY s.COMPANYCODE ORDER BY s.INNERCODE) AS rn
        FROM secumain s
        JOIN params p
          ON s.LISTEDDATE <= DATE_SUB(DATE(p.as_of_dt), INTERVAL 3 YEAR)
        JOIN asof_trade a
          ON 1 = 1
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2, 6)
          AND EXISTS (
              SELECT 1
              FROM lc_dindicesforvaluation v
              WHERE v.INNERCODE = s.INNERCODE
                AND v.TRADINGDAY = a.trade_day
          )
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              WHERE ra.INNERCODE = s.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND DATE(ra.IMPLEMENTDATE) <= DATE(p.as_of_dt)
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= p.as_of_dt
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
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_dt
         AND COALESCE(e.INSERTTIME, e.INFOPUBLDATE) <= p.as_of_dt
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_dt)
    ) t
    WHERE rn = 1
),
annual_mainindex_ver AS (
    SELECT
        m.COMPANYCODE,
        m.ENDDATE,
        m.INFOPUBLDATE,
        m.ROE,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY m.INFOPUBLDATE DESC,
                     COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                     m.JSID DESC
        ) AS rn_ver
    FROM lc_mainindexnew m
    JOIN params p
      ON m.INFOPUBLDATE <= p.as_of_dt
     AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD(DATE(p.as_of_dt), INTERVAL 1 DAY)
    WHERE m.ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
),
last_three_annual AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        ROE,
        ROW_NUMBER() OVER (
            PARTITION BY COMPANYCODE
            ORDER BY ENDDATE DESC
        ) AS rn_desc
    FROM annual_mainindex_ver
    WHERE rn_ver = 1
),
avg_roe_3y AS (
    SELECT
        COMPANYCODE,
        AVG(ROE) AS avg_roe_3y
    FROM last_three_annual
    WHERE rn_desc <= 3
    GROUP BY COMPANYCODE
    HAVING COUNT(*) = 3
       AND MIN(ENDDATE) = DATE('2022-12-31')
       AND MAX(ENDDATE) = DATE('2024-12-31')
),
valuation AS (
    SELECT
        INNERCODE,
        PE,
        PB
    FROM (
        SELECT
            v.INNERCODE,
            v.PE,
            v.PB,
            ROW_NUMBER() OVER (
                PARTITION BY v.INNERCODE
                ORDER BY v.JSID DESC
            ) AS rn
        FROM lc_dindicesforvaluation v
        JOIN asof_trade a
          ON v.TRADINGDAY = a.trade_day
    ) t
    WHERE rn = 1
)
SELECT
    sb.SECUCODE AS `代码`,
    sb.stock_name AS `名称`,
    ROUND(ar.avg_roe_3y, 4) AS `近3年平均 ROE(%)`,
    ROUND(v.PE, 4) AS `PE(TTM)`,
    ROUND(v.PB, 4) AS `PB`,
    ia.FIRSTINDUSTRYNAME AS `所属申万一级行业`
FROM stock_base sb
JOIN avg_roe_3y ar
  ON ar.COMPANYCODE = sb.COMPANYCODE
JOIN valuation v
  ON v.INNERCODE = sb.INNERCODE
LEFT JOIN industry_asof ia
  ON ia.COMPANYCODE = sb.COMPANYCODE
WHERE ar.avg_roe_3y > 18
  AND v.PE > 0
  AND v.PE < 18
  AND v.PB > 0
  AND v.PB < 1.8
ORDER BY v.PE ASC, sb.SECUCODE ASC
LIMIT 20;
