WITH params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
latest_trade AS (
    SELECT MAX(td.TRADINGDATE) AS trading_day
    FROM qt_tradingdaynew td
    JOIN params p
      ON td.TRADINGDATE <= p.as_of_date
    WHERE td.IFTRADINGDAY = 1
      AND td.SECUMARKET = 83
),
listed_asof AS (
    SELECT INNERCODE, CHANGETYPE
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY ls.CHANGEDATE DESC, ls.INSERTTIME DESC, ls.ID DESC
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
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
stock_base AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        s.LISTEDSECTOR
    FROM (
        SELECT s.*
        FROM secumain s
        JOIN params p
          ON s.LISTEDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        LEFT JOIN listed_asof la
          ON la.INNERCODE = s.INNERCODE
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2, 6, 7)
          AND la.CHANGETYPE IN (1, 3)
    ) s
),
industry_asof AS (
    SELECT COMPANYCODE, industry_name
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME AS industry_name,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR <> 7
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)

        UNION ALL

        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME AS industry_name,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
            ) AS rn
        FROM lc_stibexgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR = 7
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND e.EFFECTIVEDATE <= p.as_of_date
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    ) t
    WHERE rn = 1 AND industry_name IN ('电子', '国防军工')
),
daily_returns AS (
    SELECT p.INNERCODE, p.TRADINGDAY, p.CHANGEPCT
    FROM qt_performancedata p
    JOIN stock_base sb
      ON sb.INNERCODE = p.INNERCODE
     AND sb.LISTEDSECTOR <> 7
    CROSS JOIN latest_trade lt
    WHERE p.TRADINGDAY BETWEEN '2025-01-01' AND lt.trading_day

    UNION ALL

    SELECT
        q.INNERCODE,
        q.TRADINGDAY,
        (q.CLOSEPRICE / q.PREVCLOSEPRICE - 1) * 100 AS CHANGEPCT
    FROM lc_stibdailyquote q
    JOIN stock_base sb
      ON sb.INNERCODE = q.INNERCODE
     AND sb.LISTEDSECTOR = 7
    CROSS JOIN latest_trade lt
    WHERE q.TRADINGDAY BETWEEN '2025-01-01' AND lt.trading_day
      AND q.PREVCLOSEPRICE > 0
      AND q.CLOSEPRICE IS NOT NULL
),
daily_dispersion AS (
    SELECT
        ia.industry_name,
        p.TRADINGDAY,
        STDDEV_POP(p.CHANGEPCT) AS daily_std,
        COUNT(DISTINCT p.INNERCODE) AS stock_cnt
    FROM daily_returns p
    JOIN stock_base sb
      ON sb.INNERCODE = p.INNERCODE
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    WHERE p.CHANGEPCT IS NOT NULL
    GROUP BY ia.industry_name, p.TRADINGDAY
)
SELECT
    industry_name AS `行业名称`,
    ROUND(AVG(daily_std), 2) AS `离散度指标`
FROM daily_dispersion
GROUP BY industry_name
ORDER BY industry_name;
