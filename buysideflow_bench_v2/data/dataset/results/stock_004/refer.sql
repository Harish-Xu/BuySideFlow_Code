WITH params AS (
    SELECT DATE('2026-04-10') AS observation_date
),
listed_asof AS (
    SELECT INNERCODE
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
          ON ls.CHANGEDATE < DATE_ADD(p.observation_date, INTERVAL 1 DAY)
         AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) < DATE_ADD(p.observation_date, INTERVAL 1 DAY)
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
      AND CHANGETYPE IN (1, 3)
),
industry_asof AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYCODE
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYCODE,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC, e.ID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN params p
          ON e.INFOPUBLDATE <= p.observation_date
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.observation_date)
    ) t
    WHERE rn = 1
),
stock_pool AS (
    SELECT COMPANYCODE, SECUCODE, SECUABBR
    FROM (
        SELECT
            s.COMPANYCODE,
            s.SECUCODE,
            s.SECUABBR,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.SECUMARKET, s.SECUCODE
            ) AS rn
        FROM secumain s
        JOIN params p
          ON s.LISTEDDATE < DATE_ADD(p.observation_date, INTERVAL 1 DAY)
        JOIN listed_asof la
          ON la.INNERCODE = s.INNERCODE
        JOIN industry_asof ia
          ON ia.COMPANYCODE = s.COMPANYCODE
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2, 6)
          AND ia.FIRSTINDUSTRYCODE NOT IN ('430000', '480000', '490000')
    ) t
    WHERE rn = 1
),
annual_bs AS (
    SELECT COMPANYCODE, end_date, GOODWILL, parent_equity
    FROM (
        SELECT
            b.COMPANYCODE,
            DATE(b.ENDDATE) AS end_date,
            b.GOODWILL,
            b.SEWITHOUTMI AS parent_equity,
            ROW_NUMBER() OVER (
                PARTITION BY b.COMPANYCODE, DATE(b.ENDDATE)
                ORDER BY b.INFOPUBLDATE DESC, b.INSERTTIME DESC, b.JSID DESC, b.ID DESC
            ) AS rn
        FROM lc_balancesheetall b
        JOIN stock_pool sp
          ON sp.COMPANYCODE = b.COMPANYCODE
        JOIN params p
          ON b.INFOPUBLDATE <= p.observation_date
         AND (b.INSERTTIME IS NULL OR b.INSERTTIME < DATE_ADD(p.observation_date, INTERVAL 1 DAY))
        WHERE b.IFMERGED = 1
          AND b.BULLETINTYPE = 20
          AND b.IFCOMPLETE = 1
          AND DATE(b.ENDDATE) IN (DATE('2023-12-31'), DATE('2025-12-31'))
          AND b.GOODWILL IS NOT NULL
    ) t
    WHERE rn = 1
),
financials AS (
    SELECT
        COMPANYCODE,
        MAX(CASE WHEN end_date = DATE('2023-12-31') THEN GOODWILL END) AS goodwill_2023,
        MAX(CASE WHEN end_date = DATE('2025-12-31') THEN GOODWILL END) AS goodwill_2025,
        MAX(CASE WHEN end_date = DATE('2025-12-31') THEN parent_equity END) AS parent_equity_2025
    FROM annual_bs
    GROUP BY COMPANYCODE
)
SELECT
    sp.SECUCODE AS 证券代码,
    sp.SECUABBR AS 证券简称,
    ROUND(f.goodwill_2025 / 100000000, 2) AS `2025年末商誉`,
    ROUND(f.goodwill_2025 / f.parent_equity_2025 * 100, 2) AS 商誉占归母股东权益比例,
    ROUND((f.goodwill_2025 - f.goodwill_2023) / f.goodwill_2023 * 100, 2) AS `2023—2025年商誉变化率`
FROM stock_pool sp
JOIN financials f
  ON f.COMPANYCODE = sp.COMPANYCODE
WHERE f.goodwill_2023 > 0
  AND f.goodwill_2025 IS NOT NULL
  AND f.parent_equity_2025 > 0
  AND f.goodwill_2025 / f.parent_equity_2025 BETWEEN 0.01 AND 0.10
  AND (f.goodwill_2025 - f.goodwill_2023) / f.goodwill_2023 BETWEEN -0.05 AND 0
ORDER BY f.goodwill_2025 / f.parent_equity_2025, sp.SECUCODE;
