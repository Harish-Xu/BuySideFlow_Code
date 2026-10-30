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
        FROM lc_liststatus ls
        JOIN params p
          ON ls.CHANGEDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
industry_companies AS (
    SELECT COMPANYCODE
    FROM (
        SELECT e.COMPANYCODE, e.SECONDINDUSTRYNAME,
               ROW_NUMBER() OVER (PARTITION BY e.COMPANYCODE
                   ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC, e.ID DESC) AS rn
        FROM lc_exgindustry e
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    ) e
    WHERE rn = 1 AND SECONDINDUSTRYNAME = '休闲食品'
),
stock_base AS (
    SELECT
        s.COMPANYCODE,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name
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
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2, 6)
          AND la.CHANGETYPE IN (1, 2, 3, 6)
    ) s
    WHERE s.rn = 1
),
eligible_stocks AS (
    SELECT sb.COMPANYCODE, sb.SECUCODE, sb.stock_name
    FROM stock_base sb
    JOIN industry_companies ic
      ON ic.COMPANYCODE = sb.COMPANYCODE
),
growth_ranked AS (
    SELECT
        m.COMPANYCODE,
        m.ENDDATE,
        m.OPERATINGREVENUEYOY AS growth_rate,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY m.INFOPUBLDATE DESC,
                     CASE m.MARK WHEN 1 THEN 0 ELSE 1 END,
                     m.JSID DESC, m.ID DESC
        ) AS rn
    FROM lc_qfinancialindexnew m
    JOIN eligible_stocks es
      ON es.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
     AND m.ENDDATE <= p.as_of_date
    WHERE m.MARK IN (1, 2)
      AND m.ACCOUNTINGSTANDARDS = 1
      AND DATE_FORMAT(m.ENDDATE, '%m-%d') IN ('03-31', '06-30', '09-30', '12-31')
),
quarterly_growth AS (
    SELECT COMPANYCODE, ENDDATE, growth_rate
    FROM growth_ranked
    WHERE rn = 1
),
report_periods AS (
    SELECT COMPANYCODE, MAX(ENDDATE) AS latest_report_date
    FROM quarterly_growth
    GROUP BY COMPANYCODE
),
paired_growth AS (
    SELECT
        q.COMPANYCODE,
        q.ENDDATE AS latest_report_date,
        prev.growth_rate AS previous_growth_rate,
        q.growth_rate AS latest_growth_rate
    FROM report_periods rp
    JOIN quarterly_growth q
      ON q.COMPANYCODE = rp.COMPANYCODE AND q.ENDDATE = rp.latest_report_date
    JOIN quarterly_growth prev
      ON prev.COMPANYCODE = q.COMPANYCODE
     AND prev.ENDDATE = LAST_DAY(DATE_SUB(q.ENDDATE, INTERVAL 3 MONTH))
    WHERE prev.growth_rate IS NOT NULL
      AND q.growth_rate IS NOT NULL
      AND q.growth_rate > 0
      AND q.growth_rate > prev.growth_rate
)
SELECT
    pg.latest_report_date AS `最新报告期`,
    es.SECUCODE AS `证券代码`,
    es.stock_name AS `证券简称`,
    ROUND(pg.previous_growth_rate, 2) AS `上一季营收同比`,
    ROUND(pg.latest_growth_rate, 2) AS `最新季营收同比`,
    ROUND(pg.latest_growth_rate - pg.previous_growth_rate, 2) AS `回升幅度`
FROM paired_growth pg
JOIN eligible_stocks es
  ON es.COMPANYCODE = pg.COMPANYCODE
ORDER BY (pg.latest_growth_rate - pg.previous_growth_rate) DESC, es.SECUCODE
LIMIT 5;
