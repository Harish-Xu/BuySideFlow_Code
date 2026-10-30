-- name: stock_universe
WITH securities AS (
    SELECT INNERCODE, COMPANYCODE, SECUCODE,
           COALESCE(NULLIF(SECUABBR, ''), CHINAMEABBR, CHINAME) AS stock_name,
           LISTEDSECTOR, LISTEDDATE
    FROM secumain
    WHERE SECUCATEGORY = 1 AND SECUMARKET = 90
      AND LISTEDSECTOR = 6 AND LISTEDDATE <= DATE('$as_of_date')
), statuses AS (
    SELECT ls.INNERCODE, ls.CHANGETYPE, ls.CHANGEDATE, ls.INSERTTIME, ls.JSID, ls.ID
    FROM lc_liststatus ls JOIN securities s ON s.INNERCODE = ls.INNERCODE
    WHERE ls.CHANGETYPE BETWEEN 1 AND 9
      AND ls.CHANGEDATE <= DATE('$as_of_date') AND ls.INSERTTIME < DATE('$as_of_date')
), ranked AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY INNERCODE ORDER BY CHANGEDATE DESC, INSERTTIME DESC, JSID DESC, ID DESC
    ) AS rn FROM statuses
)
SELECT s.* FROM securities s JOIN ranked r ON r.INNERCODE = s.INNERCODE
WHERE r.rn = 1 AND r.CHANGETYPE IN (1, 3)
ORDER BY s.INNERCODE;

-- name: previous_trading_day
SELECT SECUMARKET, MAX(TRADINGDATE) AS td
FROM qt_tradingdaynew
WHERE SECUMARKET = 90 AND IFTRADINGDAY = 1
  AND TRADINGDATE >= DATE('2025-07-01') AND TRADINGDATE < DATE('$as_of_date')
GROUP BY SECUMARKET;

-- name: financial_candidates
WITH visible_quarters AS (
    SELECT q.COMPANYCODE, DATE(q.ENDDATE) AS ENDDATE, q.OPERATINGREVENUEYOY,
           q.INFOPUBLDATE,
           ROW_NUMBER() OVER (
               PARTITION BY q.COMPANYCODE, DATE(q.ENDDATE)
               ORDER BY CASE q.MARK WHEN 1 THEN 0 WHEN 2 THEN 1 ELSE 9 END,
                        q.INFOPUBLDATE DESC, q.JSID DESC, q.ID DESC
           ) AS rn
    FROM lc_qfinancialindexnew q
    WHERE q.COMPANYCODE IN ($company_values)
      AND q.ENDDATE BETWEEN DATE('2023-01-01') AND DATE('$as_of_date')
      AND q.INFOPUBLDATE < DATE('$as_of_date')
      AND q.MARK IN (1, 2)
      AND DATE_FORMAT(q.ENDDATE, '%m-%d') IN ('03-31', '06-30', '09-30', '12-31')
), report_dates AS (
    SELECT COMPANYCODE, ENDDATE FROM visible_quarters
    UNION
    SELECT COMPANYCODE, DATE(ENDDATE) FROM lc_maindatanew
    WHERE COMPANYCODE IN ($company_values)
      AND ENDDATE BETWEEN DATE('2023-01-01') AND DATE('$as_of_date')
      AND INFOPUBLDATE < DATE('$as_of_date') AND MARK IN (1, 8, 7, 6, 2)
      AND DATE_FORMAT(ENDDATE, '%m-%d') IN ('03-31', '06-30', '09-30', '12-31')
    UNION
    SELECT COMPANYCODE, DATE(ENDDATE) FROM lc_mainindexnew
    WHERE COMPANYCODE IN ($company_values)
      AND ENDDATE BETWEEN DATE('2023-01-01') AND DATE('$as_of_date')
      AND INFOPUBLDATE < DATE('$as_of_date')
      AND DATE_FORMAT(ENDDATE, '%m-%d') IN ('03-31', '06-30', '09-30', '12-31')
), latest_report AS (
    SELECT COMPANYCODE, MAX(ENDDATE) AS ENDDATE FROM report_dates GROUP BY COMPANYCODE
), offsets AS (
    SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3
), required_quarters AS (
    SELECT r.COMPANYCODE, r.ENDDATE AS latest_report_date, o.n,
           LAST_DAY(DATE_SUB(r.ENDDATE, INTERVAL (3 * o.n) MONTH)) AS required_end
    FROM latest_report r CROSS JOIN offsets o
), growth AS (
    SELECT r.COMPANYCODE, MAX(r.latest_report_date) AS latest_report_date,
           COUNT(q.OPERATINGREVENUEYOY) AS complete_quarters,
           SUM(q.OPERATINGREVENUEYOY > 30) AS high_growth_quarters,
           MAX(CASE WHEN r.n = 0 THEN q.OPERATINGREVENUEYOY END) AS latest_growth,
           MAX(CASE WHEN r.n = 1 THEN q.OPERATINGREVENUEYOY END) AS previous_growth
    FROM required_quarters r LEFT JOIN visible_quarters q
      ON q.COMPANYCODE = r.COMPANYCODE AND q.ENDDATE = r.required_end AND q.rn = 1
    GROUP BY r.COMPANYCODE
)
SELECT * FROM growth
WHERE complete_quarters = 4 AND high_growth_quarters >= 3
  AND latest_growth > previous_growth;

-- name: current_valuation
SELECT INNERCODE, PSTTM FROM (
    SELECT INNERCODE, PSTTM, ROW_NUMBER() OVER (
        PARTITION BY INNERCODE ORDER BY JSID DESC, ID DESC
    ) AS rn FROM lc_dindicesforvaluation
    WHERE INNERCODE IN ($innercode_values) AND TRADINGDAY = DATE('$trading_day')
) ranked WHERE rn = 1;

-- name: valuation_history
WITH ranked AS (
    SELECT v.INNERCODE, DATE(v.TRADINGDAY) AS TRADINGDAY, v.PSTTM,
           ROW_NUMBER() OVER (
               PARTITION BY v.INNERCODE, DATE(v.TRADINGDAY) ORDER BY v.JSID DESC, v.ID DESC
           ) AS rn
    FROM lc_dindicesforvaluation v
    JOIN secumain s ON s.INNERCODE = v.INNERCODE
    WHERE v.INNERCODE IN ($innercode_values)
      AND v.TRADINGDAY BETWEEN DATE('2023-01-01') AND DATE('$trading_day')
      AND v.TRADINGDAY >= s.LISTEDDATE
      AND EXISTS (
          SELECT 1 FROM qt_tradingdaynew t
          WHERE t.SECUMARKET = s.SECUMARKET AND t.IFTRADINGDAY = 1
            AND t.TRADINGDATE = v.TRADINGDAY
      )
)
SELECT INNERCODE, TRADINGDAY, PSTTM FROM ranked WHERE rn = 1 AND PSTTM > 0;
