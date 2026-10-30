WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
latest_trade AS (
    SELECT MAX(t.TRADINGDATE) AS trade_day
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET = 83
),
trade_days AS (
    SELECT
        d.TRADINGDATE AS trading_day,
        ROW_NUMBER() OVER (ORDER BY d.TRADINGDATE DESC) AS rn_desc
    FROM (
        SELECT DISTINCT t.TRADINGDATE
        FROM qt_tradingdaynew t
        JOIN latest_trade lt
          ON t.TRADINGDATE <= lt.trade_day
        WHERE t.IFTRADINGDAY = 1
          AND t.SECUMARKET = 83
    ) d
),
listing_cutoff AS (
    SELECT trading_day AS cutoff_day
    FROM trade_days
    WHERE rn_desc = 60
),
last_20_days AS (
    SELECT trading_day
    FROM trade_days
    WHERE rn_desc <= 20
),
stock_base AS (
    SELECT
        INNERCODE,
        COMPANYCODE,
        SECUCODE,
        stock_name
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUCODE,
            COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.INNERCODE
            ) AS rn
        FROM secumain s
        JOIN listing_cutoff lc
          ON DATE(s.LISTEDDATE) <= lc.cutoff_day
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2, 6)
    ) t
    WHERE rn = 1
),
traded_asof AS (
    SELECT DISTINCT q.INNERCODE
    FROM qt_dailyquote q
    JOIN latest_trade lt
      ON q.TRADINGDAY = lt.trade_day
),
valuation_asof AS (
    SELECT
        v.INNERCODE,
        v.NEGOTIABLEMV
    FROM lc_dindicesforvaluation v
    JOIN latest_trade lt
      ON v.TRADINGDAY = lt.trade_day
),
suspended AS (
    SELECT DISTINCT r.INNERCODE
    FROM lc_suspendresumption r
    JOIN latest_trade lt
      ON DATE(r.SUSPENDDATE) <= lt.trade_day
    WHERE COALESCE(r.INFOPUBLDATE, r.INSERTTIME, r.SUSPENDDATE)
              < DATE_ADD(lt.trade_day, INTERVAL 1 DAY)
      AND (
          r.RESUMPTIONDATE IS NULL
          OR DATE(r.RESUMPTIONDATE) > lt.trade_day
      )
),
avg_turnover_20d AS (
    SELECT
        sb.INNERCODE,
        AVG(COALESCE(q.TURNOVERVALUE, 0)) AS avg_turnover_value_20d
    FROM stock_base sb
    CROSS JOIN last_20_days d
    LEFT JOIN qt_dailyquote q
      ON q.INNERCODE = sb.INNERCODE
     AND q.TRADINGDAY = d.trading_day
    GROUP BY sb.INNERCODE
),
mainindex_ver AS (
    SELECT
        m.COMPANYCODE,
        m.ENDDATE,
        m.ROETTM,
        m.ROATTM,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn_ver
    FROM lc_mainindexnew m
    JOIN params p
      ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
     AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE m.ENDDATE <= p.as_of_date
      AND MONTH(m.ENDDATE) IN (3, 6, 9, 12)
),
latest_fin AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        ROETTM,
        ROATTM
    FROM (
        SELECT
            COMPANYCODE,
            ENDDATE,
            ROETTM,
            ROATTM,
            ROW_NUMBER() OVER (
                PARTITION BY COMPANYCODE
                ORDER BY ENDDATE DESC
            ) AS rn_company
        FROM mainindex_ver
        WHERE rn_ver = 1
    ) t
    WHERE rn_company = 1
)
SELECT
    sb.SECUCODE AS `代码`,
    sb.stock_name AS `名称`,
    ROUND(v.NEGOTIABLEMV / 100000000, 2) AS `流通市值`,
    ROUND(f.ROETTM, 2) AS `TTM ROE`,
    ROUND(f.ROATTM, 2) AS `TTM ROA`,
    ROUND(t.avg_turnover_value_20d / 100000000, 2) AS `近20日日均成交额`,
    f.ENDDATE AS `对应报告期`
FROM stock_base sb
JOIN traded_asof q
  ON q.INNERCODE = sb.INNERCODE
JOIN valuation_asof v
  ON v.INNERCODE = sb.INNERCODE
JOIN latest_fin f
  ON f.COMPANYCODE = sb.COMPANYCODE
JOIN avg_turnover_20d t
  ON t.INNERCODE = sb.INNERCODE
LEFT JOIN suspended sus
  ON sus.INNERCODE = sb.INNERCODE
CROSS JOIN latest_trade lt
WHERE NOT EXISTS (
      SELECT 1
      FROM cs_riskalert ra
      WHERE ra.INNERCODE = sb.INNERCODE
        AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
        AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE)
              < DATE_ADD(lt.trade_day, INTERVAL 1 DAY)
        AND DATE(ra.IMPLEMENTDATE) <= lt.trade_day
        AND (
            ra.REMOVEDATE IS NULL
            OR DATE(ra.REMOVEDATE) > lt.trade_day
            OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE)
                  >= DATE_ADD(lt.trade_day, INTERVAL 1 DAY)
        )
  )
  AND sus.INNERCODE IS NULL
  AND v.NEGOTIABLEMV > 0
  AND v.NEGOTIABLEMV <= 10000000000
  AND t.avg_turnover_value_20d >= 100000000
  AND f.ROETTM > 15
  AND f.ROATTM > 10
ORDER BY v.NEGOTIABLEMV ASC,
         sb.SECUCODE ASC;
