-- Five market sessions; observation-date status is independent of quote date.
WITH trading_days AS (
    SELECT DISTINCT DATE(TRADINGDATE) AS trading_day
    FROM qt_tradingdaynew
    WHERE SECUMARKET IN (83, 90) AND IFTRADINGDAY = 1
      AND TRADINGDATE < '{{cutoff}}'
    ORDER BY trading_day DESC
    LIMIT 5
), security_base AS (
    SELECT INNERCODE, SECUCODE, SECUABBR
    FROM secumain
    WHERE SECUMARKET IN (83, 90) AND SECUCATEGORY = 1
      AND LISTEDSECTOR IN (1, 2, 6)
      AND LISTEDDATE < '{{cutoff}}'
), status_ranked AS (
    SELECT ls.INNERCODE, ls.CHANGETYPE,
           ROW_NUMBER() OVER (
               PARTITION BY ls.INNERCODE
               ORDER BY ls.CHANGEDATE DESC, ls.INSERTTIME DESC, ls.ID DESC
           ) AS rn
    FROM lc_liststatus ls
    JOIN security_base s ON s.INNERCODE = ls.INNERCODE
    WHERE ls.CHANGEDATE < '{{cutoff}}'
      AND ls.INSERTTIME < '{{cutoff}}'
      AND ls.CHANGETYPE IN (1, 2, 3, 4, 5, 6, 7, 8)
), stock_base AS (
    SELECT s.*
    FROM security_base s
    LEFT JOIN status_ranked ls ON ls.INNERCODE = s.INNERCODE AND ls.rn = 1
    -- Listing date supplies the initial state if no change is recorded.
    -- Pause is not delisting; complete valid quotes are checked separately.
    WHERE COALESCE(ls.CHANGETYPE, 1) IN (1, 2, 3)
      AND NOT EXISTS (
          SELECT 1 FROM cs_riskalert r
          WHERE r.INNERCODE = s.INNERCODE
            AND r.RISKALERTTYPE IN (1, 5, 9, 10, 12, 14)
            AND r.IMPLEMENTDATE < '{{cutoff}}'
            AND COALESCE(r.IMPLANNOUCEDATE, r.IMPLEMENTDATE) < '{{cutoff}}'
            AND (r.REMOVEDATE IS NULL OR r.REMOVEDATE >= '{{cutoff}}'
                 OR r.REMOVEINFOPUBLDATE >= '{{cutoff}}')
      )
), quote_ranked AS (
    SELECT q.INNERCODE, DATE(q.TRADINGDAY) AS trading_day, q.CLOSEPRICE,
           ROW_NUMBER() OVER (
               PARTITION BY q.INNERCODE, DATE(q.TRADINGDAY)
               ORDER BY q.XGRQ DESC, q.ID DESC
           ) AS rn
    FROM qt_dailyquote q
    JOIN stock_base s ON s.INNERCODE = q.INNERCODE
    WHERE q.TRADINGDAY >= (SELECT MIN(trading_day) FROM trading_days)
      AND q.TRADINGDAY < '{{cutoff}}'
), limit_ranked AS (
    SELECT p.INNERCODE, DATE(p.TRADINGDAY) AS trading_day, p.PRICECEILING,
           ROW_NUMBER() OVER (
               PARTITION BY p.INNERCODE, DATE(p.TRADINGDAY)
               ORDER BY p.INSERTTIME DESC, p.UPDATETIME DESC, p.ID DESC
           ) AS rn
    FROM qt_pricelimit p
    JOIN stock_base s ON s.INNERCODE = p.INNERCODE
    WHERE p.TRADINGDAY >= (SELECT MIN(trading_day) FROM trading_days)
      AND p.TRADINGDAY < '{{cutoff}}'
      AND p.INSERTTIME < '{{cutoff}}'
), qualified AS (
    SELECT s.INNERCODE, s.SECUCODE, s.SECUABBR,
           SUM(CASE WHEN q.CLOSEPRICE = p.PRICECEILING THEN 1 ELSE 0 END) AS limit_count
    FROM stock_base s
    CROSS JOIN trading_days d
    LEFT JOIN quote_ranked q ON q.INNERCODE = s.INNERCODE
      AND q.trading_day = d.trading_day AND q.rn = 1
    LEFT JOIN limit_ranked p ON p.INNERCODE = s.INNERCODE
      AND p.trading_day = d.trading_day AND p.rn = 1
    GROUP BY s.INNERCODE, s.SECUCODE, s.SECUABBR
    HAVING COUNT(CASE WHEN q.CLOSEPRICE > 0 AND p.PRICECEILING > 0 THEN 1 END) = 5
), concept_ranked AS (
    SELECT cl.*,
           ROW_NUMBER() OVER (
               PARTITION BY cl.CONCEPTCODE
               ORDER BY cl.INFOPUBLDATE DESC, cl.UPDATETIME DESC, cl.ID DESC
           ) AS rn
    FROM lc_conceptlist cl
    WHERE cl.INFOPUBLDATE < '{{cutoff}}' AND cl.BEGINDATE < '{{cutoff}}'
), membership_ranked AS (
    SELECT cc.*,
           ROW_NUMBER() OVER (
               PARTITION BY cc.INNERCODE, cc.CONCEPTCODE, cc.INDATE
               ORDER BY cc.INFOPUBLDATE DESC, cc.UPDATETIME DESC, cc.ID DESC
           ) AS rn
    FROM lc_coconcept cc
    JOIN qualified q ON q.INNERCODE = cc.INNERCODE
    WHERE cc.INDATE < '{{cutoff}}' AND cc.INFOPUBLDATE < '{{cutoff}}'
), members AS (
    SELECT DISTINCT cc.INNERCODE, cl.CONCEPTCODE, cl.CONCEPTNAME
    FROM membership_ranked cc
    JOIN concept_ranked cl ON cl.CONCEPTCODE = cc.CONCEPTCODE AND cl.rn = 1
    WHERE cc.rn = 1
      AND (cc.OUTDATE IS NULL OR cc.OUTDATE >= '{{cutoff}}')
      AND cc.INFOPUBLDATE < '{{cutoff}}'
      AND cl.SUBCLASSCODE <> 1503  -- 剔除“特殊股票”子类（昨日涨停、昨日连板等按行情或证券属性生成的集合）
      AND (cl.ENDDATE IS NULL OR cl.ENDDATE >= '{{cutoff}}')
), concept_totals AS (
    SELECT m.CONCEPTCODE, m.CONCEPTNAME,
           CAST(SUM(q.limit_count) AS DECIMAL(30,16)) / (5 * COUNT(*)) AS limit_share
    FROM members m
    JOIN qualified q ON q.INNERCODE = m.INNERCODE
    GROUP BY m.CONCEPTCODE, m.CONCEPTNAME
    HAVING COUNT(*) >= 10 AND SUM(q.limit_count) > 0
), winner AS (
    SELECT * FROM concept_totals
    ORDER BY limit_share DESC, CONCEPTNAME ASC, CONCEPTCODE ASC
    LIMIT 1
)
SELECT w.CONCEPTNAME AS `概念名称`,
       ROUND(w.limit_share * 100, 2) AS `概念涨停股票日占比`,
       q.SECUCODE AS `股票代码`, q.SECUABBR AS `股票简称`,
       q.limit_count AS `5日收盘涨停次数`
FROM winner w
JOIN members m ON m.CONCEPTCODE = w.CONCEPTCODE
JOIN qualified q ON q.INNERCODE = m.INNERCODE
WHERE q.limit_count > 0
ORDER BY q.limit_count DESC, q.SECUCODE ASC
LIMIT 3;
