WITH
params AS (
    SELECT
        DATE('2025-11-30') AS as_of_date,
        DATE('2024-12-31') AS annual_end,
        DATE('2025-09-30') AS latest_financial_end
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
        FROM lc_liststatus ls
        JOIN params p
          ON ls.CHANGEDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
stock_seed AS (
    SELECT
        s.COMPANYCODE,
        MIN(s.INNERCODE) AS INNERCODE
    FROM secumain s
    LEFT JOIN listed_asof la
      ON la.INNERCODE = s.INNERCODE
    JOIN params p
      ON s.LISTEDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR IN (1, 2, 6)
      AND la.CHANGETYPE IN (1, 3)
      AND NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = s.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND DATE(ra.IMPLEMENTDATE) <= p.as_of_date
            AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
            AND (
                ra.REMOVEDATE IS NULL
                OR DATE(ra.REMOVEDATE) > p.as_of_date
                OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) >= DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
            )
      )
    GROUP BY s.COMPANYCODE
),
stock_base AS (
    SELECT
        s.COMPANYCODE,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS company
    FROM stock_seed ss
    JOIN secumain s
      ON s.INNERCODE = ss.INNERCODE
),
industry_asof AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(e.INSERTTIME, e.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
          AND e.FIRSTINDUSTRYNAME IN ('房地产', '建筑装饰', '建筑材料')
    ) t
    WHERE rn = 1
),
candidate AS (
    SELECT sb.COMPANYCODE, sb.SECUCODE, sb.company, ia.FIRSTINDUSTRYNAME
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
),
annual_key AS (
    SELECT b.COMPANYCODE, MAX(b.JSID) AS max_jsid
    FROM lc_balancesheetall b
    JOIN candidate c
      ON c.COMPANYCODE = b.COMPANYCODE
    JOIN params p
      ON b.INFOPUBLDATE <= p.as_of_date
     AND COALESCE(b.INSERTTIME, b.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE b.ENDDATE = p.annual_end
      AND b.IFMERGED = 1
      AND b.IFADJUSTED = 2
      AND b.ACCOUNTINGSTANDARDS = 1
      AND b.DEFERREDTAXASSETS IS NOT NULL
      AND b.SEWITHOUTMI IS NOT NULL
    GROUP BY b.COMPANYCODE
),
annual_balance AS (
    SELECT
        b.COMPANYCODE,
        b.DEFERREDTAXASSETS / NULLIF(b.SEWITHOUTMI, 0) AS annual_ratio
    FROM lc_balancesheetall b
    JOIN annual_key k
      ON k.COMPANYCODE = b.COMPANYCODE
     AND k.max_jsid = b.JSID
),
latest_key AS (
    SELECT b.COMPANYCODE, MAX(b.JSID) AS max_jsid
    FROM lc_balancesheetall b
    JOIN candidate c
      ON c.COMPANYCODE = b.COMPANYCODE
    JOIN params p
      ON b.INFOPUBLDATE <= p.as_of_date
     AND COALESCE(b.INSERTTIME, b.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE b.ENDDATE = p.latest_financial_end
      AND b.IFMERGED = 1
      AND b.IFADJUSTED = 2
      AND b.ACCOUNTINGSTANDARDS = 1
      AND b.DEFERREDTAXASSETS IS NOT NULL
      AND b.SEWITHOUTMI IS NOT NULL
    GROUP BY b.COMPANYCODE
),
latest_balance AS (
    SELECT
        b.COMPANYCODE,
        b.DEFERREDTAXASSETS / NULLIF(b.SEWITHOUTMI, 0) AS latest_ratio
    FROM lc_balancesheetall b
    JOIN latest_key k
      ON k.COMPANYCODE = b.COMPANYCODE
     AND k.max_jsid = b.JSID
),
observed AS (
    SELECT
        c.FIRSTINDUSTRYNAME AS industry,
        c.SECUCODE AS code,
        c.company,
        ab.annual_ratio,
        lb.latest_ratio,
        lb.latest_ratio - ab.annual_ratio AS ratio_change,
        CASE
            WHEN lb.latest_ratio - ab.annual_ratio > 0.02 THEN '高占比继续抬升'
            WHEN lb.latest_ratio - ab.annual_ratio < -0.02 THEN '高占比边际回落'
            ELSE '高占比高位持平'
        END AS pressure_state
    FROM candidate c
    JOIN annual_balance ab
      ON ab.COMPANYCODE = c.COMPANYCODE
    JOIN latest_balance lb
      ON lb.COMPANYCODE = c.COMPANYCODE
    WHERE lb.latest_ratio > 0.10
),
state_stats AS (
    SELECT
        industry,
        pressure_state,
        COUNT(*) AS company_count
    FROM observed
    GROUP BY industry, pressure_state
),
industry_pool AS (
    SELECT
        industry,
        COUNT(*) AS pool_count
    FROM observed
    GROUP BY industry
),
rising_ranked AS (
    SELECT
        o.industry,
        o.code,
        o.company,
        o.annual_ratio,
        o.latest_ratio,
        o.ratio_change,
        ROW_NUMBER() OVER (
            PARTITION BY o.industry
            ORDER BY o.latest_ratio DESC, o.code
        ) AS rn
    FROM observed o
    WHERE o.pressure_state = '高占比继续抬升'
)
SELECT
    ss.industry AS `行业`,
    ss.pressure_state AS `税项压力状态`,
    ss.company_count AS `公司数`,
    ROUND(ss.company_count / NULLIF(ip.pool_count, 0) * 100, 4) AS `占行业高压力池比例(%)`,
    CASE WHEN ss.pressure_state = '高占比继续抬升' THEN rr.code END AS `代码`,
    CASE WHEN ss.pressure_state = '高占比继续抬升' THEN rr.company END AS `公司`,
    CASE WHEN ss.pressure_state = '高占比继续抬升' THEN ROUND(rr.annual_ratio * 100, 4) END AS `2024年报比例(%)`,
    CASE WHEN ss.pressure_state = '高占比继续抬升' THEN ROUND(rr.latest_ratio * 100, 4) END AS `最新比例(%)`,
    CASE WHEN ss.pressure_state = '高占比继续抬升' THEN ROUND(rr.ratio_change * 100, 4) END AS `变化(pct)`
FROM state_stats ss
JOIN industry_pool ip
  ON ip.industry = ss.industry
LEFT JOIN rising_ranked rr
  ON rr.industry = ss.industry
 AND rr.rn <= 5
 AND ss.pressure_state = '高占比继续抬升'
ORDER BY FIELD(ss.industry, '房地产', '建筑装饰', '建筑材料'),
         FIELD(ss.pressure_state, '高占比继续抬升', '高占比高位持平', '高占比边际回落'),
         `最新比例(%)` DESC,
         `代码`;
