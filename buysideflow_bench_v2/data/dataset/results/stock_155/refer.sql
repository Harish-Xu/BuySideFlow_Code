-- name: month_ends
SELECT MAX(TRADINGDATE) AS month_end
FROM qt_tradingdaynew
WHERE SECUMARKET = 83
  AND IFTRADINGDAY = 1
  AND TRADINGDATE <= DATE('{as_of_date}')
  AND LAST_DAY(TRADINGDATE) <= DATE('{as_of_date}')
GROUP BY YEAR(TRADINGDATE), MONTH(TRADINGDATE)
ORDER BY month_end;

-- name: base_panel
WITH
schedule AS (
    {schedule_rows}
),
stock_base AS (
    SELECT
        sm.INNERCODE,
        sm.COMPANYCODE,
        sm.SECUCODE,
        sm.LISTEDDATE,
        ROW_NUMBER() OVER (PARTITION BY sm.INNERCODE ORDER BY sm.SECUCODE) AS rn
    FROM secumain sm
    WHERE sm.SECUCATEGORY = 1
      AND sm.SECUMARKET IN (83, 90)
      AND sm.LISTEDSECTOR IN (1, 2, 6)
      AND sm.COMPANYCODE IS NOT NULL
),
stock_companies AS (
    SELECT DISTINCT COMPANYCODE
    FROM stock_base
    WHERE rn = 1
),
growth_ranked AS (
    SELECT
        s.obs_date,
        g.COMPANYCODE,
        g.OPERATINGREVENUEGROWRATE AS revenue_yoy,
        ROW_NUMBER() OVER (
            PARTITION BY s.obs_date, g.COMPANYCODE
            ORDER BY
                g.ENDDATE DESC,
                g.INFOPUBLDATE DESC,
                g.INSERTTIME DESC,
                g.JSID DESC,
                g.ID DESC
        ) AS rn
    FROM schedule s
    CROSS JOIN stock_companies sc
    JOIN lc_mainindexnew g
      ON g.COMPANYCODE = sc.COMPANYCODE
     AND g.INFOPUBLDATE < DATE_ADD(s.obs_date, INTERVAL 1 DAY)
    WHERE g.OPERATINGREVENUEGROWRATE IS NOT NULL
),
latest_growth AS (
    SELECT obs_date, COMPANYCODE, revenue_yoy
    FROM growth_ranked
    WHERE rn = 1
),
latest_list_status AS (
    SELECT obs_date, INNERCODE
    FROM (
        SELECT
            s.obs_date,
            ls.INNERCODE,
            ls.CHANGETYPE,
            ROW_NUMBER() OVER (
                PARTITION BY s.obs_date, ls.INNERCODE
                ORDER BY ls.CHANGEDATE DESC,
                         COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
                         ls.JSID DESC,
                         ls.ID DESC
            ) AS rn
        FROM schedule s
        JOIN stock_base sb
          ON sb.rn = 1
         AND sb.LISTEDDATE < DATE_ADD(s.obs_date, INTERVAL 1 DAY)
        JOIN lc_liststatus ls
          ON ls.INNERCODE = sb.INNERCODE
         AND ls.SECUMARKET IN (83, 90)
         AND ls.CHANGETYPE BETWEEN 1 AND 9
         AND ls.CHANGEDATE < DATE_ADD(s.obs_date, INTERVAL 1 DAY)
         AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME < DATE_ADD(s.obs_date, INTERVAL 1 DAY))
    ) ranked
    WHERE rn = 1
      AND CHANGETYPE IN (1, 3)
)
SELECT
    s.obs_date,
    sb.INNERCODE,
    sb.COMPANYCODE,
    v.TOTALMV AS total_market_value,
    v.PB,
    lg.revenue_yoy,
    p1.BACKWARDPRICE / NULLIF(p0.BACKWARDPRICE, 0) - 1 AS stock_return
FROM schedule s
JOIN stock_base sb
  ON sb.rn = 1
 AND sb.LISTEDDATE < DATE_ADD(s.obs_date, INTERVAL 1 DAY)
JOIN latest_list_status ls
  ON ls.obs_date = s.obs_date
 AND ls.INNERCODE = sb.INNERCODE
JOIN qt_dailyquote q0
  ON q0.TRADINGDAY = s.obs_date
 AND q0.INNERCODE = sb.INNERCODE
JOIN qt_performancedata p0
  ON p0.TRADINGDAY = s.obs_date
 AND p0.INNERCODE = sb.INNERCODE
JOIN qt_performancedata p1
  ON p1.TRADINGDAY = s.month_end
 AND p1.INNERCODE = sb.INNERCODE
LEFT JOIN lc_dindicesforvaluation v
  ON v.TRADINGDAY = s.obs_date
 AND v.INNERCODE = sb.INNERCODE
LEFT JOIN latest_growth lg
  ON lg.obs_date = s.obs_date
 AND lg.COMPANYCODE = sb.COMPANYCODE
WHERE q0.CLOSEPRICE > 0
  AND q0.TURNOVERVALUE > 0
  AND p0.BACKWARDPRICE > 0
  AND p1.BACKWARDPRICE > 0
ORDER BY s.obs_date, sb.INNERCODE;

-- name: st_risk
WITH obs_dates AS (
    {obs_date_rows}
)
SELECT DISTINCT od.obs_date, ra.INNERCODE
FROM obs_dates od
JOIN cs_riskalert ra
  ON ra.IMPLEMENTDATE < DATE_ADD(od.obs_date, INTERVAL 1 DAY)
 AND COALESCE(ra.IMPLANNOUCEDATE, ra.IMPLEMENTDATE) < DATE_ADD(od.obs_date, INTERVAL 1 DAY)
WHERE ra.INNERCODE IN ({innercodes})
  AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
  AND (
      ra.REMOVEDATE IS NULL
      OR ra.REMOVEDATE >= DATE_ADD(od.obs_date, INTERVAL 1 DAY)
      OR (
          ra.REMOVEINFOPUBLDATE IS NOT NULL
          AND ra.REMOVEINFOPUBLDATE >= DATE_ADD(od.obs_date, INTERVAL 1 DAY)
      )
  );
