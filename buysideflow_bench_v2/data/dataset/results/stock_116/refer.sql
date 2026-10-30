WITH recent_days AS (
    SELECT TRADINGDATE
    FROM qt_tradingdaynew
    WHERE SECUMARKET = 83 AND IFTRADINGDAY = 1 AND TRADINGDATE <= '2025-12-30'
    ORDER BY TRADINGDATE DESC LIMIT 250
), calendar AS (
    SELECT TRADINGDATE, ROW_NUMBER() OVER (ORDER BY TRADINGDATE DESC) AS day_no
    FROM recent_days
), stocks AS (
    SELECT s.INNERCODE, s.SECUCODE, s.SECUABBR
    FROM secumain s
    WHERE s.SECUCATEGORY = 1 AND s.SECUMARKET IN (83,90)
      AND s.LISTEDSECTOR IN (1,2,6)
      AND s.LISTEDDATE <= (SELECT MIN(TRADINGDATE) FROM calendar)
      AND (SELECT l.CHANGETYPE FROM lc_liststatus l
           WHERE l.INNERCODE=s.INNERCODE AND l.CHANGEDATE < '2025-12-31'
             AND l.CHANGETYPE BETWEEN 1 AND 9
           ORDER BY l.CHANGEDATE DESC,l.JSID DESC,l.ID DESC LIMIT 1) IN (1,2,3,6)
      AND NOT EXISTS (
          SELECT 1 FROM cs_riskalert r
          WHERE r.INNERCODE=s.INNERCODE AND r.RISKALERTTYPE IN (1,5,12,14)
            AND r.IMPLEMENTDATE < '2025-12-31'
            AND COALESCE(r.IMPLANNOUCEDATE,r.INSERTTIME) < '2025-12-31'
            AND (r.REMOVEDATE IS NULL OR r.REMOVEDATE >= '2025-12-31'
                 OR COALESCE(r.REMOVEINFOPUBLDATE,r.INSERTTIME) >= '2025-12-31')
      )
), prices AS (
    SELECT s.*,p.TRADINGDAY,p.BACKWARDPRICE,c.day_no
    FROM stocks s JOIN qt_performancedata p ON p.INNERCODE=s.INNERCODE
    JOIN calendar c ON c.TRADINGDATE=p.TRADINGDAY
    WHERE p.BACKWARDPRICE > 0
), stats AS (
    SELECT INNERCODE,COUNT(DISTINCT TRADINGDAY) AS n,
           MAX(CASE WHEN day_no=1 THEN BACKWARDPRICE END) AS obs_price,
           MAX(CASE WHEN day_no>1 THEN BACKWARDPRICE END) AS prior_high
    FROM prices GROUP BY INNERCODE
), selected AS (
    SELECT p.INNERCODE,p.SECUCODE,p.SECUABBR,s.obs_price,
           MIN(p.TRADINGDAY) AS first_high_day, MAX(p.day_no)-1 AS gap
    FROM prices p JOIN stats s ON s.INNERCODE=p.INNERCODE
    WHERE s.n=250 AND s.obs_price > s.prior_high AND p.day_no>1
      AND p.BACKWARDPRICE=s.prior_high
    GROUP BY p.INNERCODE,p.SECUCODE,p.SECUABBR,s.obs_price
)
SELECT SECUCODE AS `股票代码`, SECUABBR AS `股票简称`,
       ROUND(obs_price,4) AS `当日后复权收盘价`,
       DATE_FORMAT(first_high_day,'%Y-%m-%d') AS `前高首次出现日期`,
       gap AS `间隔交易日数`
FROM selected WHERE gap>60 ORDER BY gap DESC, SECUCODE;
