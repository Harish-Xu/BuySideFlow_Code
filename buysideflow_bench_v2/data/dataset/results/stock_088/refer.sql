WITH params AS (
    SELECT DATE('2025-07-07') AS as_of_date
),
trade_asof AS (
    SELECT MAX(t.TRADINGDATE) AS trade_day
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET IN (83, 90)
),
index_info AS (
    SELECT INNERCODE AS index_innercode
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '399101'
      AND SECUMARKET IN (83, 90)
),
index_components AS (
    SELECT DISTINCT
        ic.SECUINNERCODE AS innercode
    FROM lc_indexcomponent ic
    JOIN index_info ii
      ON ii.index_innercode = ic.INDEXINNERCODE
    JOIN trade_asof ta
      ON ic.INDATE <= ta.trade_day
    WHERE ic.OUTDATE IS NULL OR ic.OUTDATE > ta.trade_day
),
component_valuation_raw AS (
    SELECT
        s.INNERCODE,
        LPAD(s.SECUCODE, 6, '0') AS stock_code,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name,
        v.NEGOTIABLEMV,
        ta.trade_day,
        ROW_NUMBER() OVER (
            PARTITION BY s.INNERCODE
            ORDER BY COALESCE(v.INSERTTIME, v.TRADINGDAY) DESC, v.JSID DESC
        ) AS valuation_rn
    FROM index_components ic
    JOIN secumain s
      ON s.INNERCODE = ic.innercode
    JOIN trade_asof ta
      ON 1 = 1
    JOIN lc_dindicesforvaluation v
      ON v.INNERCODE = s.INNERCODE
     AND v.TRADINGDAY = ta.trade_day
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND v.NEGOTIABLEMV IS NOT NULL
),
ranked_raw_candidates AS (
    SELECT
        INNERCODE,
        stock_code,
        stock_name,
        NEGOTIABLEMV,
        trade_day,
        ROW_NUMBER() OVER (ORDER BY NEGOTIABLEMV ASC, stock_code) AS rn
    FROM component_valuation_raw
    WHERE valuation_rn = 1
),
top100 AS (
    SELECT *
    FROM ranked_raw_candidates
    WHERE rn <= 100
),
risk_alert_active AS (
    SELECT DISTINCT t.INNERCODE
    FROM top100 t
    JOIN cs_riskalert ra
      ON ra.INNERCODE = t.INNERCODE
     AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
     AND DATE(COALESCE(ra.IMPLANNOUCEDATE, ra.IMPLEMENTDATE)) <= t.trade_day
     AND DATE(ra.IMPLEMENTDATE) <= t.trade_day
     AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE)
         < DATE_ADD(t.trade_day, INTERVAL 1 DAY)
     AND (
         ra.REMOVEDATE IS NULL
         OR DATE(ra.REMOVEDATE) > t.trade_day
         OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE)
             >= DATE_ADD(t.trade_day, INTERVAL 1 DAY)
     )
),
limit_status AS (
    SELECT
        t.INNERCODE,
        COALESCE(p.SURGEDLIMIT, 0) AS surged_limit,
        COALESCE(p.DECLINELIMIT, 0) AS decline_limit
    FROM top100 t
    JOIN qt_performancedata p
      ON p.INNERCODE = t.INNERCODE
     AND p.TRADINGDAY = t.trade_day
),
eligible_top100 AS (
    SELECT
        t.*,
        ROW_NUMBER() OVER (ORDER BY t.NEGOTIABLEMV ASC, t.stock_code) AS eligible_rn
    FROM top100 t
    JOIN limit_status ls
      ON ls.INNERCODE = t.INNERCODE
    LEFT JOIN risk_alert_active ra
      ON ra.INNERCODE = t.INNERCODE
    WHERE ls.surged_limit = 0
      AND ls.decline_limit = 0
      AND ra.INNERCODE IS NULL
)
SELECT
    t.stock_code AS `股票代码`,
    t.stock_name AS `股票简称`,
    ROUND(t.NEGOTIABLEMV, 2) AS `流通市值（元）`,
    t.trade_day AS `日期`
FROM eligible_top100 t
WHERE t.eligible_rn <= 30
ORDER BY t.NEGOTIABLEMV ASC, t.stock_code;
