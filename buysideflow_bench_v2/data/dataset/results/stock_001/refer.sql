-- name: month_ends
SELECT DATE_FORMAT(TRADINGDATE, '%Y-%m') AS month_id, DATE(MAX(TRADINGDATE)) AS obs_date
FROM qt_tradingdaynew
WHERE IFTRADINGDAY = 1
  AND SECUMARKET IN (83, 90)
  AND TRADINGDATE BETWEEN DATE('$start_date') AND DATE('$end_date')
GROUP BY DATE_FORMAT(TRADINGDATE, '%Y-%m')
ORDER BY obs_date

-- name: trading_days
SELECT DISTINCT DATE(TRADINGDATE) AS trade_date
FROM qt_tradingdaynew
WHERE IFTRADINGDAY = 1
  AND SECUMARKET IN (83, 90)
  AND TRADINGDATE >= DATE('$start_date')
  AND TRADINGDATE <= DATE_ADD(DATE('$end_date'), INTERVAL 60 DAY)
ORDER BY trade_date

-- name: universe_asof
WITH params AS (
    SELECT DATE('$obs_date') AS obs_date
),
target_index AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '000906'
    ORDER BY CASE WHEN SECUMARKET = 83 THEN 0 ELSE 1 END, INNERCODE
    LIMIT 1
),
stock_base AS (
    SELECT DISTINCT
        p.obs_date,
        s.INNERCODE,
        s.COMPANYCODE, s.SECUCODE
    FROM params p
    JOIN target_index ti
      ON 1 = 1
    JOIN lc_indexcomponent ic
      ON ic.INDEXINNERCODE = ti.INNERCODE
     AND DATE(ic.INDATE) <= p.obs_date
     AND (ic.OUTDATE IS NULL OR DATE(ic.OUTDATE) > p.obs_date)
    JOIN secumain s
      ON s.INNERCODE = ic.SECUINNERCODE
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR IN (1, 2, 6)
      AND DATE(s.LISTEDDATE) <= p.obs_date
),
industry_ranked AS (
    SELECT
        sb.COMPANYCODE,
        i.FIRSTINDUSTRYNAME,
        ROW_NUMBER() OVER (
            PARTITION BY sb.COMPANYCODE
            ORDER BY
                DATE(i.INFOPUBLDATE) DESC,
                i.JSID DESC,
                i.ID DESC
        ) AS rn
    FROM stock_base sb
    JOIN lc_exgindustry i
      ON i.COMPANYCODE = sb.COMPANYCODE
    JOIN params p
      ON 1 = 1
    WHERE i.STANDARD = 38
      AND i.FIRSTINDUSTRYNAME IS NOT NULL
      AND DATE(i.INFOPUBLDATE) <= p.obs_date
      AND (i.CANCELDATE IS NULL OR DATE(i.CANCELDATE) > p.obs_date)
)
SELECT
    sb.obs_date,
    sb.INNERCODE,
    sb.COMPANYCODE, sb.SECUCODE
FROM stock_base sb
JOIN industry_ranked ir
  ON ir.COMPANYCODE = sb.COMPANYCODE
 AND ir.rn = 1
JOIN params p
  ON 1 = 1
WHERE ir.FIRSTINDUSTRYNAME NOT IN ('银行', '非银金融', '房地产')
  AND NOT EXISTS (
      SELECT 1
      FROM cs_riskalert ra
      WHERE ra.INNERCODE = sb.INNERCODE
        AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
        AND DATE(ra.IMPLEMENTDATE) <= p.obs_date
        AND DATE(COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE)) <= p.obs_date
        AND (
            ra.REMOVEDATE IS NULL
            OR DATE(ra.REMOVEDATE) > p.obs_date
            OR DATE(COALESCE(ra.REMOVEINFOPUBLDATE, ra.REMOVEDATE)) > p.obs_date
        )
  )
  AND NOT EXISTS (
      SELECT 1
      FROM lc_suspendresumption sr
      WHERE sr.INNERCODE = sb.INNERCODE
        AND DATE(sr.SUSPENDDATE) <= p.obs_date
        AND DATE(COALESCE(sr.INFOPUBLDATE, sr.INSERTTIME, sr.SUSPENDDATE)) <= p.obs_date
        AND (sr.RESUMPTIONDATE IS NULL OR DATE(sr.RESUMPTIONDATE) > p.obs_date)
  )
ORDER BY sb.INNERCODE

-- name: financial_asof
WITH main_ranked AS (
    SELECT *
    FROM (
        SELECT
            COMPANYCODE,
            ENDDATE,
            INFOPUBLDATE,
            ROETTM,
            ROW_NUMBER() OVER (
                PARTITION BY COMPANYCODE, ENDDATE
                ORDER BY INFOPUBLDATE DESC, INSERTTIME DESC, JSID DESC, ID DESC
            ) AS rn
        FROM lc_mainindexnew
        WHERE COMPANYCODE IN ($code_values)
          AND ENDDATE BETWEEN DATE('$fin_start_date') AND DATE('$obs_date')
          AND INFOPUBLDATE < DATE_ADD(DATE('$obs_date'), INTERVAL 1 DAY)
          AND MONTH(ENDDATE) IN (3, 6, 9, 12)
    ) ranked
    WHERE rn = 1
),
report_periods AS (
    SELECT COMPANYCODE, ENDDATE, MAX(INFOPUBLDATE) AS publish_date
    FROM lc_maindatanew
    WHERE COMPANYCODE IN ($code_values)
      AND ENDDATE BETWEEN DATE('$fin_start_date') AND DATE('$obs_date')
      AND INFOPUBLDATE < DATE_ADD(DATE('$obs_date'), INTERVAL 1 DAY)
      AND CAST(BULLETINTYPE AS UNSIGNED) = 20
      AND INFOSOURCECODE IN (110101, 110102, 110103, 110104)
      AND MONTH(ENDDATE) IN (3, 6, 9, 12)
    GROUP BY COMPANYCODE, ENDDATE
),
periods AS (
    SELECT COMPANYCODE, ENDDATE, MAX(publish_date) AS publish_date
    FROM (
        SELECT COMPANYCODE, ENDDATE, INFOPUBLDATE AS publish_date FROM main_ranked
        UNION ALL
        SELECT COMPANYCODE, ENDDATE, publish_date FROM report_periods
    ) visible
    GROUP BY COMPANYCODE, ENDDATE
)
SELECT p.COMPANYCODE, DATE(p.ENDDATE) AS end_date,
       DATE(COALESCE(m.INFOPUBLDATE, p.publish_date)) AS publish_date, m.ROETTM
FROM periods p
LEFT JOIN main_ranked m ON m.COMPANYCODE = p.COMPANYCODE AND m.ENDDATE = p.ENDDATE
ORDER BY p.COMPANYCODE, p.ENDDATE

-- name: quotes
SELECT
    INNERCODE,
    DATE(TRADINGDAY) AS trade_date,
    BACKWARDPRICE AS adjusted_close
FROM qt_performancedata
WHERE TRADINGDAY IN ($date_values)
  AND INNERCODE IN ($innercode_values)
  AND BACKWARDPRICE IS NOT NULL
  AND BACKWARDPRICE > 0
ORDER BY INNERCODE, TRADINGDAY
