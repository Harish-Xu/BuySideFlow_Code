-- Read-only blocks used by refer.py; dates and IDs come from the fixed task.
-- name: index_id
SELECT INNERCODE FROM secumain
WHERE SECUCODE = '000300' AND SECUCATEGORY = 4 AND SECUMARKET = 83

-- name: calendar
SELECT DISTINCT TRADINGDATE AS trade_date FROM qt_tradingdaynew
WHERE SECUMARKET IN (83, 90) AND IFTRADINGDAY = 1
  AND TRADINGDATE BETWEEN '2024-04-01' AND '2025-10-31'
ORDER BY TRADINGDATE

-- name: universe
SELECT DISTINCT s.INNERCODE, s.COMPANYCODE, s.SECUCODE, s.LISTEDSECTOR
FROM lc_indexcomponent c
JOIN secumain s ON s.INNERCODE = c.SECUINNERCODE
WHERE c.INDEXINNERCODE = $index_id
  AND c.INDATE <= '$observation'
  AND (c.OUTDATE IS NULL OR c.OUTDATE > '$observation')
  AND s.SECUCATEGORY = 1 AND s.SECUMARKET IN (83, 90)
  AND s.LISTEDSECTOR <> 7

-- name: financials
WITH ranked AS (
  SELECT COMPANYCODE, ENDDATE, INFOPUBLDATE, MARK,
         NPFROMPARENTCOMPANYOWNERS AS profit,
         ROW_NUMBER() OVER (
           PARTITION BY COMPANYCODE, ENDDATE
           ORDER BY CASE MARK WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                              WHEN 6 THEN 4 WHEN 2 THEN 5 END,
                    INFOPUBLDATE DESC, JSID DESC, ID DESC
         ) AS rn
  FROM lc_maindatanew
  WHERE COMPANYCODE IN ($company_ids)
    AND ENDDATE BETWEEN '2021-01-01' AND '$observation'
    AND INFOPUBLDATE < '$cutoff' AND MARK IN (1, 8, 7, 6, 2)
)
SELECT COMPANYCODE, ENDDATE, INFOPUBLDATE, MARK, profit
FROM ranked WHERE rn = 1

-- name: pe_start
SELECT INNERCODE, MIN(TRADINGDAY) AS first_date
FROM lc_dindicesforvaluation
WHERE INNERCODE IN ($inner_ids) AND TRADINGDAY <= '$valuation'
  AND PE IS NOT NULL
GROUP BY INNERCODE

-- name: pe_window
WITH ranked AS (
  SELECT INNERCODE, TRADINGDAY, PE,
         ROW_NUMBER() OVER (
           PARTITION BY INNERCODE, TRADINGDAY ORDER BY JSID DESC, ID DESC
         ) AS rn
  FROM lc_dindicesforvaluation
  WHERE INNERCODE IN ($inner_ids)
    AND TRADINGDAY BETWEEN '$window_start' AND '$valuation'
)
SELECT INNERCODE, TRADINGDAY, PE FROM ranked WHERE rn = 1

-- name: stock_prices
WITH ranked AS (
  SELECT INNERCODE, TRADINGDAY, BACKWARDPRICE,
         ROW_NUMBER() OVER (
           PARTITION BY INNERCODE, TRADINGDAY ORDER BY JSID DESC, ID DESC
         ) AS rn
  FROM qt_performancedata
  WHERE INNERCODE IN ($inner_ids) AND TRADINGDAY IN ($price_dates)
)
SELECT INNERCODE, TRADINGDAY, BACKWARDPRICE FROM ranked WHERE rn = 1

-- name: benchmark_prices
SELECT TRADINGDAY, CLOSEPRICE FROM qt_indexquote
WHERE INNERCODE = $index_id AND TRADINGDAY IN ($price_dates)
ORDER BY TRADINGDAY
