-- name: stock_universe
SELECT
    s.INNERCODE,
    s.COMPANYCODE,
    s.SECUMARKET,
    LPAD(s.SECUCODE, 6, '0') AS stock_code,
    COALESCE(NULLIF(s.SECUABBR, ''), NULLIF(s.CHINAMEABBR, ''), s.CHINAME) AS stock_name,
    s.LISTEDDATE
FROM secumain s
WHERE s.SECUCATEGORY = 1
  AND s.SECUMARKET IN (83, 90)
  AND s.LISTEDSECTOR IN (1, 2, 6)
  AND s.LISTEDDATE <= DATE('$as_of_date')
ORDER BY s.INNERCODE;

-- name: valuation_today
SELECT INNERCODE, TOTALMV, EVTOEBITDA
FROM (
    SELECT
        v.INNERCODE,
        v.TOTALMV,
        v.EVTOEBITDA,
        ROW_NUMBER() OVER (
            PARTITION BY v.INNERCODE
            ORDER BY v.JSID DESC, v.ID DESC
        ) AS rn
    FROM lc_dindicesforvaluation v
    WHERE v.INNERCODE IN ($innercode_values)
      AND v.TRADINGDAY = DATE('$as_of_date')
) ranked
WHERE rn = 1
  AND TOTALMV >= 10000000000
  AND EVTOEBITDA > 0
  AND EVTOEBITDA < 8;

-- name: list_status_asof
SELECT INNERCODE
FROM (
    SELECT
        ls.INNERCODE,
        ls.CHANGETYPE,
        ROW_NUMBER() OVER (
            PARTITION BY ls.INNERCODE
            ORDER BY ls.CHANGEDATE DESC, ls.INSERTTIME DESC, ls.JSID DESC, ls.ID DESC
        ) AS rn
    FROM lc_liststatus ls
    WHERE ls.INNERCODE IN ($innercode_values)
      AND ls.CHANGETYPE BETWEEN 1 AND 9
      AND ls.CHANGEDATE <= DATE('$as_of_date')
      AND ls.INSERTTIME < DATE_ADD(DATE('$as_of_date'), INTERVAL 1 DAY)
) ranked
WHERE rn = 1
  AND CHANGETYPE IN (1, 3);

-- name: industry_asof
SELECT COMPANYCODE, FIRSTINDUSTRYNAME
FROM (
    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYNAME,
        e.CANCELDATE,
        ROW_NUMBER() OVER (
            PARTITION BY e.COMPANYCODE
            ORDER BY e.INFOPUBLDATE DESC, e.INSERTTIME DESC, e.JSID DESC, e.ID DESC
        ) AS rn
    FROM lc_exgindustry e
    WHERE e.COMPANYCODE IN ($company_values)
      AND e.STANDARD = 38
      AND e.INFOPUBLDATE < DATE_ADD(DATE('$as_of_date'), INTERVAL 1 DAY)
) ranked
WHERE rn = 1
  AND (CANCELDATE IS NULL OR CANCELDATE > DATE('$as_of_date'))
  AND FIRSTINDUSTRYNAME NOT IN ('银行', '非银金融');

-- name: valuation_percentiles
WITH current_values AS (
    $candidate_values
),
history_ranked AS (
    SELECT
        v.INNERCODE,
        DATE(v.TRADINGDAY) AS TRADINGDAY,
        v.EVTOEBITDA,
        ROW_NUMBER() OVER (
            PARTITION BY v.INNERCODE, DATE(v.TRADINGDAY)
            ORDER BY v.JSID DESC, v.ID DESC
        ) AS rn
    FROM lc_dindicesforvaluation v
    JOIN current_values c
      ON c.INNERCODE = v.INNERCODE
    JOIN secumain s ON s.INNERCODE = v.INNERCODE
    WHERE v.TRADINGDAY BETWEEN DATE_SUB(DATE('$as_of_date'), INTERVAL 3 YEAR)
                           AND DATE('$as_of_date')
      AND EXISTS (
          SELECT 1 FROM qt_tradingdaynew t
          WHERE t.SECUMARKET = s.SECUMARKET AND t.IFTRADINGDAY = 1
            AND t.TRADINGDATE = v.TRADINGDAY
      )
)
SELECT
    c.INNERCODE,
    AVG(CASE WHEN h.EVTOEBITDA <= c.current_ev
             THEN 1.000000000000 ELSE 0.000000000000 END) AS ev_percentile
FROM current_values c
JOIN history_ranked h
  ON h.INNERCODE = c.INNERCODE
 AND h.rn = 1
 AND h.EVTOEBITDA > 0
GROUP BY c.INNERCODE;

-- name: latest_fcf
WITH report_dates AS (
    SELECT COMPANYCODE, DATE(ENDDATE) AS ENDDATE
    FROM lc_maindatanew
    WHERE COMPANYCODE IN ($company_values)
      AND ENDDATE BETWEEN DATE('2023-01-01') AND DATE('$as_of_date')
      AND INFOPUBLDATE < DATE_ADD(DATE('$as_of_date'), INTERVAL 1 DAY)
      AND MARK IN (1, 8, 7, 6, 2)
      AND DATE_FORMAT(ENDDATE, '%m-%d') IN ('03-31', '06-30', '09-30', '12-31')
    UNION
    SELECT COMPANYCODE, DATE(ENDDATE)
    FROM lc_mainindexnew
    WHERE COMPANYCODE IN ($company_values)
      AND ENDDATE BETWEEN DATE('2023-01-01') AND DATE('$as_of_date')
      AND INFOPUBLDATE < DATE_ADD(DATE('$as_of_date'), INTERVAL 1 DAY)
      AND DATE_FORMAT(ENDDATE, '%m-%d') IN ('03-31', '06-30', '09-30', '12-31')
    UNION
    SELECT COMPANYCODE, DATE(ENDDATE)
    FROM lc_qfinancialindexnew
    WHERE COMPANYCODE IN ($company_values)
      AND ENDDATE BETWEEN DATE('2023-01-01') AND DATE('$as_of_date')
      AND INFOPUBLDATE < DATE_ADD(DATE('$as_of_date'), INTERVAL 1 DAY)
      AND MARK IN (1, 8, 7, 6, 2)
      AND DATE_FORMAT(ENDDATE, '%m-%d') IN ('03-31', '06-30', '09-30', '12-31')
),
latest_report AS (
    SELECT COMPANYCODE, MAX(ENDDATE) AS ENDDATE
    FROM report_dates GROUP BY COMPANYCODE
),
visible_fcf AS (
    SELECT
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS ENDDATE,
        m.FREECASHFLOW,
        m.INFOPUBLDATE,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC,
                     m.INSERTTIME DESC,
                     m.JSID DESC,
                     m.ID DESC
        ) AS rn
    FROM lc_mainindexnew m
    WHERE m.COMPANYCODE IN ($company_values)
      AND m.ENDDATE BETWEEN DATE('2022-01-01') AND DATE('$as_of_date')
      AND m.INFOPUBLDATE < DATE_ADD(DATE('$as_of_date'), INTERVAL 1 DAY)
),
period_fcf AS (
    SELECT COMPANYCODE, ENDDATE, FREECASHFLOW, INFOPUBLDATE
    FROM visible_fcf
    WHERE rn = 1
),
ttm_fcf AS (
    SELECT
        report.COMPANYCODE,
        report.ENDDATE,
        cur.INFOPUBLDATE,
        CASE
            WHEN MONTH(cur.ENDDATE) = 12 THEN cur.FREECASHFLOW
            ELSE cur.FREECASHFLOW + annual.FREECASHFLOW - prior_same.FREECASHFLOW
        END AS FREECASHFLOW
    FROM latest_report report
    LEFT JOIN period_fcf cur
      ON cur.COMPANYCODE = report.COMPANYCODE
     AND cur.ENDDATE = report.ENDDATE
    LEFT JOIN period_fcf annual
      ON annual.COMPANYCODE = cur.COMPANYCODE
     AND annual.ENDDATE = STR_TO_DATE(
            CONCAT(YEAR(cur.ENDDATE) - 1, '-12-31'),
            '%Y-%m-%d'
         )
    LEFT JOIN period_fcf prior_same
      ON prior_same.COMPANYCODE = cur.COMPANYCODE
     AND prior_same.ENDDATE = DATE_SUB(cur.ENDDATE, INTERVAL 1 YEAR)
)
SELECT COMPANYCODE, ENDDATE, INFOPUBLDATE, FREECASHFLOW
FROM ttm_fcf;
