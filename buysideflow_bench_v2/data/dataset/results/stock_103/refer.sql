-- name: trade_days
SELECT trade_date
FROM (
    SELECT DISTINCT DATE(t.TRADINGDATE) AS trade_date
    FROM qt_tradingdaynew t
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET = 83
      AND t.TRADINGDATE <= DATE('$as_of_date')
) d
ORDER BY trade_date DESC
LIMIT 60;

-- name: security_pool
WITH base AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        LPAD(s.SECUCODE, 6, '0') AS stock_code,
        COALESCE(NULLIF(s.SECUABBR, ''), NULLIF(s.CHINAMEABBR, ''),
                 NULLIF(s.CHINAME, ''), s.SECUCODE) AS stock_name
    FROM secumain s
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR IN (1, 2, 6)
      AND s.LISTEDDATE <= DATE_SUB(DATE('$as_of_date'), INTERVAL 1 YEAR)
      AND s.COMPANYCODE IS NOT NULL
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
        JOIN base b
          ON b.INNERCODE = ls.INNERCODE
        WHERE ls.CHANGETYPE BETWEEN 1 AND 9
          AND ls.CHANGEDATE < DATE_ADD(DATE('$as_of_date'), INTERVAL 1 DAY)
          AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE)
              < DATE_ADD(DATE('$as_of_date'), INTERVAL 1 DAY)
    ) ranked
    WHERE rn = 1
)
SELECT b.INNERCODE, b.COMPANYCODE, b.stock_code, b.stock_name
FROM base b
JOIN listed_asof l
  ON l.INNERCODE = b.INNERCODE
 AND l.CHANGETYPE IN (1, 3)
ORDER BY b.INNERCODE;

-- name: active_risk_alerts
SELECT DISTINCT ra.INNERCODE
FROM cs_riskalert ra
WHERE ra.INNERCODE IN ($innercodes)
  AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
  AND ra.IMPLEMENTDATE < DATE_ADD(DATE('$as_of_date'), INTERVAL 1 DAY)
  AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE)
      < DATE_ADD(DATE('$as_of_date'), INTERVAL 1 DAY)
  AND (
      ra.REMOVEDATE IS NULL
      OR ra.REMOVEDATE >= DATE_ADD(DATE('$as_of_date'), INTERVAL 1 DAY)
      OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE)
          >= DATE_ADD(DATE('$as_of_date'), INTERVAL 1 DAY)
  );

-- name: valuation_data
SELECT INNERCODE, NEGOTIABLEMV
FROM (
    SELECT
        v.INNERCODE,
        v.NEGOTIABLEMV,
        ROW_NUMBER() OVER (
            PARTITION BY v.INNERCODE
            ORDER BY COALESCE(v.INSERTTIME, v.TRADINGDAY) DESC,
                     v.JSID DESC,
                     v.ID DESC
        ) AS rn
    FROM lc_dindicesforvaluation v
    WHERE v.INNERCODE IN ($innercodes)
      AND v.TRADINGDAY >= DATE('$as_of_date')
      AND v.TRADINGDAY < DATE_ADD(DATE('$as_of_date'), INTERVAL 1 DAY)
      AND COALESCE(v.INSERTTIME, v.TRADINGDAY)
          < DATE_ADD(DATE('$as_of_date'), INTERVAL 1 DAY)
      AND v.NEGOTIABLEMV >= 5000000000
) ranked
WHERE rn = 1;

-- name: financial_data
SELECT COMPANYCODE, ROE
FROM (
    SELECT
        m.COMPANYCODE,
        m.ROE,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE
            ORDER BY m.INFOPUBLDATE DESC,
                     COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                     m.JSID DESC,
                     m.ID DESC
        ) AS rn
    FROM lc_mainindexnew m
    WHERE m.COMPANYCODE IN ($companycodes)
      AND m.ENDDATE >= DATE('$report_date')
      AND m.ENDDATE < DATE_ADD(DATE('$report_date'), INTERVAL 1 DAY)
      AND m.INFOPUBLDATE < DATE_ADD(DATE('$as_of_date'), INTERVAL 1 DAY)
      AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE)
          < DATE_ADD(DATE('$as_of_date'), INTERVAL 1 DAY)
      AND m.ROE IS NOT NULL
) ranked
WHERE rn = 1
  AND ROE > 0;

-- name: quote_data
WITH daily_ranked AS (
    SELECT
        q.INNERCODE,
        DATE(q.TRADINGDAY) AS trade_date,
        q.CLOSEPRICE,
        q.LOWPRICE,
        q.TURNOVERVOLUME,
        ROW_NUMBER() OVER (
            PARTITION BY q.INNERCODE, DATE(q.TRADINGDAY)
            ORDER BY q.JSID DESC, q.ID DESC
        ) AS rn
    FROM qt_dailyquote q
    WHERE q.INNERCODE IN ($innercodes)
      AND q.TRADINGDAY >= DATE('$start_date')
      AND q.TRADINGDAY < DATE_ADD(DATE('$end_date'), INTERVAL 1 DAY)
),
performance_ranked AS (
    SELECT
        p.INNERCODE,
        DATE(p.TRADINGDAY) AS trade_date,
        p.BACKWARDPRICE,
        ROW_NUMBER() OVER (
            PARTITION BY p.INNERCODE, DATE(p.TRADINGDAY)
            ORDER BY p.JSID DESC, p.ID DESC
        ) AS rn
    FROM qt_performancedata p
    WHERE p.INNERCODE IN ($innercodes)
      AND p.TRADINGDAY >= DATE('$start_date')
      AND p.TRADINGDAY < DATE_ADD(DATE('$end_date'), INTERVAL 1 DAY)
)
SELECT
    q.INNERCODE,
    q.trade_date,
    q.TURNOVERVOLUME,
    p.BACKWARDPRICE AS adjusted_close,
    q.LOWPRICE * p.BACKWARDPRICE / q.CLOSEPRICE AS adjusted_low
FROM daily_ranked q
JOIN performance_ranked p
  ON p.INNERCODE = q.INNERCODE
 AND p.trade_date = q.trade_date
 AND p.rn = 1
WHERE q.rn = 1
  AND q.CLOSEPRICE > 0
  AND q.LOWPRICE > 0
  AND p.BACKWARDPRICE > 0
ORDER BY q.INNERCODE, q.trade_date;
