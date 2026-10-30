-- name: trading_days
SELECT DISTINCT DATE(td.TRADINGDATE) AS trade_date
FROM qt_tradingdaynew td
WHERE td.IFTRADINGDAY = 1
  AND td.SECUMARKET IN (83, 90)
  AND td.TRADINGDATE BETWEEN DATE('${calendar_start_date}') AND DATE('${calendar_end_date}')
ORDER BY trade_date;

-- name: stock_master
SELECT
    s.INNERCODE,
    s.COMPANYCODE,
    LPAD(s.SECUCODE, 6, '0') AS SECUCODE,
    COALESCE(NULLIF(s.CHINAMEABBR, ''), NULLIF(s.SECUABBR, ''), NULLIF(s.CHINAME, ''), s.SECUCODE) AS stock_name,
    DATE(s.LISTEDDATE) AS listed_date,
    s.LISTEDSECTOR
FROM secumain s
WHERE s.SECUCATEGORY = 1
  AND s.COMPANYCODE IS NOT NULL
  AND s.SECUMARKET IN (83, 90)
  AND s.LISTEDSECTOR IN (1, 2, 6)
  AND DATE(s.LISTEDDATE) <= DATE_SUB(DATE('${as_of_date}'), INTERVAL 2 YEAR)
  AND (
      SELECT ls.CHANGETYPE
      FROM lc_liststatus ls
      WHERE ls.INNERCODE = s.INNERCODE
        AND ls.SECUMARKET IN (83, 90)
        AND ls.CHANGETYPE BETWEEN 1 AND 9
        AND DATE(ls.CHANGEDATE) <= DATE('${as_of_date}')
        AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) < DATE_ADD(DATE('${as_of_date}'), INTERVAL 1 DAY)
      ORDER BY
        ls.CHANGEDATE DESC,
        COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
        ls.JSID DESC
      LIMIT 1
  ) IN (1, 3)
  AND NOT EXISTS (
      SELECT 1
      FROM cs_riskalert ra
      WHERE ra.INNERCODE = s.INNERCODE
        AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
        AND DATE(ra.IMPLEMENTDATE) <= DATE('${as_of_date}')
        AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE)
            < DATE_ADD(DATE('${as_of_date}'), INTERVAL 1 DAY)
        AND (
            ra.REMOVEDATE IS NULL
            OR DATE(ra.REMOVEDATE) > DATE('${as_of_date}')
            OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE)
                >= DATE_ADD(DATE('${as_of_date}'), INTERVAL 1 DAY)
        )
  )
ORDER BY s.INNERCODE;

-- name: financial_sync
SELECT
    q.COMPANYCODE,
    DATE(q.ENDDATE) AS end_date,
    DATE(q.INFOPUBLDATE) AS pub_date,
    q.OPERATINGREVENUEYOY,
    q.NPPARENTCOMPANYCUTYOY,
    q.NETOPERATECASHFLOWYOY,
    q.MARK,
    q.INSERTTIME,
    q.ID,
    q.JSID
FROM lc_qfinancialindexnew q
WHERE q.COMPANYCODE IN (${company_values})
  AND q.ENDDATE BETWEEN DATE('${financial_start_date}') AND DATE('${report_end_date}')
  AND q.INFOPUBLDATE < DATE_ADD(DATE('${as_of_date}'), INTERVAL 1 DAY)
  AND q.MARK IN (1, 8, 7, 6, 2)
  AND DATE_FORMAT(q.ENDDATE, '%m-%d') IN ('03-31', '06-30', '09-30', '12-31')
  AND (
        q.OPERATINGREVENUEYOY IS NOT NULL
     OR q.NPPARENTCOMPANYCUTYOY IS NOT NULL
     OR q.NETOPERATECASHFLOWYOY IS NOT NULL
  )
ORDER BY q.COMPANYCODE, q.ENDDATE, q.INFOPUBLDATE, q.INSERTTIME, q.ID, q.JSID;

-- name: stock_quotes
SELECT
    q.INNERCODE,
    DATE(q.TRADINGDAY) AS trade_date,
    q.TURNOVERVALUE,
    p.BACKWARDPRICE
FROM qt_dailyquote q
LEFT JOIN qt_performancedata p
  ON p.INNERCODE = q.INNERCODE
 AND p.TRADINGDAY = q.TRADINGDAY
WHERE q.INNERCODE IN (${innercode_values})
  AND q.TRADINGDAY BETWEEN DATE('${quote_start_date}') AND DATE('${quote_end_date}')
ORDER BY q.INNERCODE, q.TRADINGDAY;

-- name: benchmark_quotes
SELECT
    DATE(qi.TRADINGDAY) AS trade_date,
    qi.CLOSEPRICE AS close_price
FROM secumain sm
JOIN qt_indexquote qi
  ON qi.INNERCODE = sm.INNERCODE
WHERE sm.SECUCATEGORY = 4
  AND sm.SECUCODE = '${benchmark_code}'
  AND qi.TRADINGDAY BETWEEN DATE('${quote_start_date}') AND DATE('${quote_end_date}')
  AND qi.CLOSEPRICE IS NOT NULL
  AND qi.CLOSEPRICE > 0
ORDER BY qi.TRADINGDAY;
