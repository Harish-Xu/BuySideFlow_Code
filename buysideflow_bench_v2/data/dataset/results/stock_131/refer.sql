WITH
params AS (
    SELECT DATE('2025-05-31') AS as_of_date
),
obs_trade AS (
    SELECT MAX(TRADINGDATE) AS trading_day
    FROM qt_tradingdaynew
    WHERE IFTRADINGDAY = 1
      AND SECUMARKET = 83
      AND TRADINGDATE <= (SELECT as_of_date FROM params)
),
obs_month_start AS (
    SELECT DATE_FORMAT(trading_day, '%Y-%m-01') AS current_month_start
    FROM obs_trade
),
target_months AS (
    SELECT
        DATE_SUB(current_month_start, INTERVAL 1 MONTH) AS t_minus_1_month,
        DATE_SUB(current_month_start, INTERVAL 13 MONTH) AS t_minus_13_month
    FROM obs_month_start
),
target_dates AS (
    SELECT
        MAX(
            CASE
                WHEN t.TRADINGDATE >= tm.t_minus_13_month
                 AND t.TRADINGDATE < DATE_ADD(tm.t_minus_13_month, INTERVAL 1 MONTH)
                THEN t.TRADINGDATE
            END
        ) AS start_date,
        MAX(
            CASE
                WHEN t.TRADINGDATE >= tm.t_minus_1_month
                 AND t.TRADINGDATE < DATE_ADD(tm.t_minus_1_month, INTERVAL 1 MONTH)
                THEN t.TRADINGDATE
            END
        ) AS end_date
    FROM qt_tradingdaynew t
    CROSS JOIN target_months tm
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET = 83
),
stock_data AS (
    SELECT
        s.SECUCODE,
        s.SECUABBR,
        q.TRADINGDAY,
        q.BACKWARDPRICE AS CLOSEPRICE
    FROM qt_performancedata q
    JOIN secumain s
      ON s.INNERCODE = q.INNERCODE
    JOIN target_dates td
      ON q.TRADINGDAY IN (td.start_date, td.end_date)
    WHERE s.SECUCODE = '601012'
      AND q.BACKWARDPRICE IS NOT NULL
      AND q.BACKWARDPRICE > 0

    UNION ALL

    SELECT
        s.SECUCODE,
        s.SECUABBR,
        q.TRADINGDAY,
        q.CLOSEPRICE
    FROM qt_indexquote q
    JOIN secumain s
      ON s.INNERCODE = q.INNERCODE
    JOIN target_dates td
      ON q.TRADINGDAY IN (td.start_date, td.end_date)
    WHERE s.SECUCODE = '801730'
      AND q.CLOSEPRICE IS NOT NULL
      AND q.CLOSEPRICE > 0
),
price_points AS (
    SELECT
        sd.SECUCODE,
        sd.SECUABBR,
        MAX(CASE WHEN sd.TRADINGDAY = td.end_date THEN sd.CLOSEPRICE END) AS t_minus_1_price,
        MAX(CASE WHEN sd.TRADINGDAY = td.start_date THEN sd.CLOSEPRICE END) AS t_minus_13_price
    FROM stock_data sd
    CROSS JOIN target_dates td
    GROUP BY sd.SECUCODE, sd.SECUABBR
),
momentum_calc AS (
    SELECT
        SECUCODE,
        SECUABBR,
        t_minus_1_price / NULLIF(t_minus_13_price, 0) - 1 AS price_momentum
    FROM price_points
    WHERE t_minus_1_price IS NOT NULL
      AND t_minus_13_price IS NOT NULL
      AND t_minus_13_price > 0
)
SELECT
    mc.SECUCODE AS `证券代码`,
    mc.SECUABBR AS `证券简称`,
    ROUND(mc.price_momentum, 4) AS `价格动量`,
    ROUND(mc.price_momentum - idx.price_momentum, 4) AS `行业相对动量`
FROM momentum_calc mc
JOIN momentum_calc idx
  ON idx.SECUCODE = '801730'
WHERE mc.SECUCODE = '601012'
ORDER BY mc.SECUCODE;
