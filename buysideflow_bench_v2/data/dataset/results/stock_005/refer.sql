-- name: month_ends
SELECT
    DATE_FORMAT(td.TRADINGDATE, '%Y-%m') AS month_id,
    DATE(MAX(td.TRADINGDATE)) AS obs_date
FROM qt_tradingdaynew td
WHERE td.SECUMARKET = 83
  AND td.IFTRADINGDAY = 1
  AND td.TRADINGDATE BETWEEN DATE('${start_date}') AND DATE('${end_date}')
GROUP BY DATE_FORMAT(td.TRADINGDATE, '%Y-%m')
ORDER BY obs_date

-- name: trading_days
SELECT DATE(td.TRADINGDATE) AS trade_date
FROM qt_tradingdaynew td
WHERE td.SECUMARKET = 83
  AND td.IFTRADINGDAY = 1
  AND td.TRADINGDATE BETWEEN DATE('${calendar_start_date}') AND DATE('${calendar_end_date}')
ORDER BY trade_date

-- name: component_history
SELECT
    ic.SECUINNERCODE AS INNERCODE,
    s.COMPANYCODE,
    s.SECUCODE,
    COALESCE(NULLIF(s.CHINAMEABBR, ''), NULLIF(s.SECUABBR, ''), s.SECUCODE) AS stock_name,
    DATE(s.LISTEDDATE) AS listed_date,
    DATE(ic.INDATE) AS in_date,
    DATE(ic.OUTDATE) AS out_date
FROM lc_indexcomponent ic
JOIN secumain idx
  ON idx.INNERCODE = ic.INDEXINNERCODE
 AND idx.SECUCODE = '000510'
 AND idx.SECUCATEGORY = 4
JOIN secumain s
  ON s.INNERCODE = ic.SECUINNERCODE
WHERE s.SECUCATEGORY = 1
  AND s.SECUMARKET IN (83, 90)
  AND s.LISTEDDATE IS NOT NULL
  AND ic.INDATE <= DATE('${end_date}')
  AND (ic.OUTDATE IS NULL OR ic.OUTDATE >= DATE('${start_date}'))
ORDER BY ic.INDATE, ic.SECUINNERCODE

-- name: list_status_history
SELECT
    ls.INNERCODE,
    DATE(ls.CHANGEDATE) AS change_date,
    DATE(COALESCE(ls.INSERTTIME, ls.CHANGEDATE)) AS visible_date,
    ls.CHANGETYPE AS change_type,
    ls.JSID,
    ls.ID
FROM (
    SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID, SECUMARKET
    FROM lc_liststatus
    UNION ALL
    SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID, 83 AS SECUMARKET
    FROM lc_stibliststatus
) ls
JOIN secumain s
  ON s.INNERCODE = ls.INNERCODE
WHERE s.SECUCATEGORY = 1
  AND s.SECUMARKET IN (83, 90)
  AND ls.SECUMARKET IN (83, 90)
  AND ls.CHANGETYPE BETWEEN 1 AND 9
  AND ls.CHANGEDATE IS NOT NULL
  AND ls.CHANGEDATE <= DATE('${end_date}')
  AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) <= DATE('${end_date}')
ORDER BY ls.INNERCODE, ls.CHANGEDATE, visible_date, ls.JSID, ls.ID

-- name: risk_alerts
SELECT
    INNERCODE,
    DATE(IMPLEMENTDATE) AS implement_date,
    DATE(REMOVEDATE) AS remove_date,
    DATE(COALESCE(IMPLANNOUCEDATE, INSERTTIME, IMPLEMENTDATE)) AS implement_visible_date,
    DATE(COALESCE(REMOVEINFOPUBLDATE, REMOVEDATE)) AS remove_visible_date,
    RISKALERTTYPE AS risk_alert_type
FROM cs_riskalert
WHERE RISKALERTTYPE IN (1, 5, 12, 14)
  AND COALESCE(IMPLANNOUCEDATE, INSERTTIME, IMPLEMENTDATE) <= DATE('${end_date}')
  AND IMPLEMENTDATE <= DATE('${end_date}')
  AND (REMOVEDATE IS NULL OR REMOVEDATE >= DATE('${start_date}'))

-- name: liquidity_quotes
SELECT
    q.INNERCODE,
    DATE(q.TRADINGDAY) AS trade_date,
    q.CLOSEPRICE,
    q.TURNOVERVALUE
FROM qt_dailyquote q
WHERE q.INNERCODE IN (${innercode_values})
  AND q.TRADINGDAY IN (${date_values})
ORDER BY q.INNERCODE, q.TRADINGDAY

-- name: valuation_history
SELECT
    v.INNERCODE,
    DATE(v.TRADINGDAY) AS trade_date,
    v.TOTALMV
FROM lc_dindicesforvaluation v
WHERE v.INNERCODE IN (${innercode_values})
  AND v.TRADINGDAY IN (${date_values})
  AND v.TOTALMV IS NOT NULL
  AND v.TOTALMV > 0
ORDER BY v.TRADINGDAY, v.INNERCODE

-- name: financial_cashflow
SELECT
    m.COMPANYCODE,
    DATE(m.ENDDATE) AS end_date,
    DATE(m.INFOPUBLDATE) AS pub_date,
    m.MARK,
    CASE
        WHEN m.MARK = 1 THEN 5
        WHEN m.MARK = 8 THEN 4
        WHEN m.MARK = 7 THEN 3
        WHEN m.MARK = 6 THEN 2
        WHEN m.MARK = 2 THEN 1
        ELSE 0
    END AS statement_priority,
    CASE WHEN m.ACCOUNTINGSTANDARDS = 1 THEN 1 ELSE 0 END AS accounting_priority,
    m.NETOPERATECASHFLOW,
    m.UPDATETIME,
    m.JSID,
    m.ID
FROM lc_maindatanew m
WHERE m.COMPANYCODE IN (${company_values})
  AND m.ENDDATE BETWEEN DATE('${financial_start_date}') AND DATE('${end_date}')
  AND m.INFOPUBLDATE <= DATE('${end_date}')
  AND MONTH(m.ENDDATE) IN (3, 6, 9, 12)
  AND m.MARK IN (1, 8, 7, 6, 2)
  AND m.NETOPERATECASHFLOW IS NOT NULL
ORDER BY m.COMPANYCODE, m.MARK, m.ENDDATE, m.INFOPUBLDATE, m.UPDATETIME, m.JSID, m.ID

-- name: dividend_history
SELECT
    d.INNERCODE,
    s.COMPANYCODE,
    DATE(d.ENDDATE) AS end_date,
    DATE(COALESCE(d.LATESTINFOPUBLDATE, d.SMDECIPUBLDATE, d.DIVIINTENTPUBLDATE, d.XGRQ)) AS pub_date,
    COALESCE(
        d.TOTALCASHDIVICOMRMB,
        d.CASHDIVIASHARE,
        d.CASHDIVIRMB,
        0
    ) AS cash_dividend,
    d.IFDIVIDEND,
    d.XGRQ,
    d.JSID,
    d.ID
FROM lc_dividend d
JOIN secumain s
  ON s.INNERCODE = d.INNERCODE
WHERE d.INNERCODE IN (${innercode_values})
  AND d.ENDDATE BETWEEN DATE('${financial_start_date}') AND DATE('${end_date}')
  AND MONTH(d.ENDDATE) = 12
  AND COALESCE(d.LATESTINFOPUBLDATE, d.SMDECIPUBLDATE, d.DIVIINTENTPUBLDATE, d.XGRQ) <= DATE('${end_date}')
ORDER BY s.COMPANYCODE, d.ENDDATE, pub_date, d.XGRQ, d.JSID, d.ID

-- name: stock_quotes
SELECT
    q.INNERCODE,
    DATE(q.TRADINGDAY) AS trade_date,
    q.OPENPRICE,
    q.CLOSEPRICE,
    p.BACKWARDPRICE
FROM qt_dailyquote q
LEFT JOIN qt_performancedata p
  ON p.INNERCODE = q.INNERCODE
 AND p.TRADINGDAY = q.TRADINGDAY
WHERE q.INNERCODE IN (${innercode_values})
  AND q.TRADINGDAY IN (${date_values})
ORDER BY q.INNERCODE, q.TRADINGDAY

-- name: benchmark_quotes
SELECT
    DATE(q.TRADINGDAY) AS trade_date,
    q.OPENPRICE,
    q.CLOSEPRICE
FROM qt_indexquote q
JOIN secumain idx
  ON idx.INNERCODE = q.INNERCODE
 AND idx.SECUCODE = '000510'
 AND idx.SECUCATEGORY = 4
WHERE q.TRADINGDAY IN (${date_values})
ORDER BY q.TRADINGDAY
