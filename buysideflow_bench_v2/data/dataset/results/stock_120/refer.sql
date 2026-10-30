WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
recent_days AS (
    SELECT DISTINCT t.TRADINGDATE
    FROM qt_tradingdaynew t JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.SECUMARKET = 83 AND t.IFTRADINGDAY = 1
    ORDER BY t.TRADINGDATE DESC
    LIMIT 27
),
calendar AS (
    SELECT TRADINGDATE, ROW_NUMBER() OVER (ORDER BY TRADINGDATE DESC) AS day_no
    FROM recent_days
),
candidates AS (
    SELECT d.TRADINGDATE AS event_date, d.day_no, s.INNERCODE,
           s.SECUCODE, s.SECUABBR
    FROM calendar d
    JOIN qt_dailyquote q ON q.TRADINGDAY = d.TRADINGDATE
    JOIN secumain s ON s.INNERCODE = q.INNERCODE
    JOIN qt_pricelimit pl ON pl.INNERCODE = q.INNERCODE
                        AND pl.TRADINGDAY = q.TRADINGDAY
    CROSS JOIN params p
    WHERE d.day_no <= 20
      AND s.SECUMARKET IN (83, 90) AND s.SECUCATEGORY = 1
      AND s.LISTEDSECTOR IN (1, 2) AND s.LISTEDDATE <= d.TRADINGDATE
      AND (
          SELECT ls.CHANGETYPE
          FROM lc_liststatus ls
          WHERE ls.INNERCODE = s.INNERCODE
            AND ls.SECUMARKET = s.SECUMARKET
            AND ls.CHANGEDATE <= d.TRADINGDATE
            AND ls.CHANGETYPE IN (1, 2, 3, 4, 5, 6)
          ORDER BY ls.CHANGEDATE DESC, ls.JSID DESC, ls.ID DESC
          LIMIT 1
      ) IN (1, 2, 3, 6)
      AND NOT EXISTS (
          SELECT 1 FROM cs_riskalert ra
          WHERE ra.INNERCODE = s.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND ra.IMPLEMENTDATE <= d.TRADINGDATE
            AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME) < DATE_ADD(d.TRADINGDATE, INTERVAL 1 DAY)
            AND (
                ra.REMOVEDATE IS NULL
                OR ra.REMOVEDATE > d.TRADINGDATE
                OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME) >= DATE_ADD(d.TRADINGDATE, INTERVAL 1 DAY)
            )
      )
      AND q.PREVCLOSEPRICE > 0 AND pl.PRICECEILING > 0
      AND q.LOWPRICE > 0 AND q.LOWPRICE < q.PREVCLOSEPRICE * 0.97
      AND q.CLOSEPRICE = pl.PRICECEILING
),
window_stats AS (
    SELECT c.event_date, c.INNERCODE,
           SUM(CASE WHEN pd.SURGEDLIMIT = 1 THEN 1 ELSE 0 END) AS limit_days
    FROM candidates c
    JOIN calendar d ON d.day_no BETWEEN c.day_no AND c.day_no + 7
    LEFT JOIN qt_performancedata pd
      ON pd.INNERCODE = c.INNERCODE AND pd.TRADINGDAY = d.TRADINGDATE
    GROUP BY c.event_date, c.INNERCODE
    HAVING COUNT(*) = 8
)
SELECT c.event_date AS `交易日期`,
       c.SECUCODE AS `股票代码`, c.SECUABBR AS `股票简称`,
       CAST(w.limit_days AS UNSIGNED) AS `近8个交易日收盘涨停天数`
FROM candidates c
JOIN window_stats w ON w.INNERCODE = c.INNERCODE AND w.event_date = c.event_date
ORDER BY c.event_date DESC, c.SECUCODE;
