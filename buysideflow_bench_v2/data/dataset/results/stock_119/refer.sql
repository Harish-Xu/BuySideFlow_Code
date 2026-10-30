WITH
params AS (
    SELECT DATE('2025-06-30') AS observation_date
),
recent_days AS (
    SELECT t.TRADINGDATE
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE < p.observation_date
    WHERE t.SECUMARKET = 83
      AND t.IFTRADINGDAY = 1
    ORDER BY t.TRADINGDATE DESC
    LIMIT 5
),
price_candidates AS (
    SELECT
        q.TRADINGDAY AS event_date,
        s.INNERCODE,
        s.SECUMARKET,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name,
        q.OPENPRICE,
        q.CLOSEPRICE,
        q.HIGHPRICE,
        pl.PRICECEILING
    FROM recent_days d
    JOIN qt_dailyquote q
      ON q.TRADINGDAY = d.TRADINGDATE
    JOIN secumain s
      ON s.INNERCODE = q.INNERCODE
    JOIN qt_pricelimit pl
      ON pl.INNERCODE = q.INNERCODE
     AND pl.TRADINGDAY = q.TRADINGDAY
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR IN (1, 2)
      AND s.LISTEDDATE <= q.TRADINGDAY
      AND q.PREVCLOSEPRICE > 0
      AND q.OPENPRICE > 0
      AND q.CLOSEPRICE > 0
      AND q.HIGHPRICE > 0
      AND pl.PRICECEILING > 0
      AND q.HIGHPRICE = pl.PRICECEILING
      AND q.CLOSEPRICE <= q.HIGHPRICE * 0.98
      AND q.CLOSEPRICE > q.PREVCLOSEPRICE
      AND q.CLOSEPRICE < q.OPENPRICE
),
list_status AS (
    SELECT event_date, INNERCODE, CHANGETYPE
    FROM (
        SELECT
            c.event_date,
            c.INNERCODE,
            ls.CHANGETYPE,
            ROW_NUMBER() OVER (
                PARTITION BY c.event_date, c.INNERCODE
                ORDER BY DATE(ls.CHANGEDATE) DESC,
                         COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
                         ls.JSID DESC,
                         ls.ID DESC
            ) AS rn
        FROM price_candidates c
        JOIN lc_liststatus ls
          ON ls.INNERCODE = c.INNERCODE
         AND ls.SECUMARKET = c.SECUMARKET
         AND DATE(ls.CHANGEDATE) <= c.event_date
        JOIN params p
          ON COALESCE(ls.INSERTTIME, ls.CHANGEDATE) < p.observation_date
        WHERE ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
eligible AS (
    SELECT c.*
    FROM price_candidates c
    JOIN list_status ls
      ON ls.event_date = c.event_date
     AND ls.INNERCODE = c.INNERCODE
     AND ls.CHANGETYPE IN (1, 2, 3, 6)
    CROSS JOIN params p
    WHERE NOT EXISTS (
        SELECT 1
        FROM cs_riskalert ra
        WHERE ra.INNERCODE = c.INNERCODE
          AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
          AND DATE(ra.IMPLEMENTDATE) <= c.event_date
          AND COALESCE(ra.IMPLANNOUCEDATE, ra.IMPLEMENTDATE) < p.observation_date
          AND COALESCE(ra.INSERTTIME, ra.IMPLANNOUCEDATE, ra.IMPLEMENTDATE) < p.observation_date
          AND (
              ra.REMOVEDATE IS NULL
              OR DATE(ra.REMOVEDATE) > c.event_date
              OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.REMOVEDATE) >= p.observation_date
          )
    )
)
SELECT
    event_date AS `日期`,
    SECUCODE AS `股票代码`,
    stock_name AS `股票简称`,
    ROUND(OPENPRICE, 4) AS `开盘价`,
    ROUND(CLOSEPRICE, 4) AS `收盘价`,
    ROUND(HIGHPRICE, 4) AS `最高价`,
    ROUND(PRICECEILING, 4) AS `涨停价`
FROM eligible
ORDER BY event_date DESC, SECUCODE ASC;
