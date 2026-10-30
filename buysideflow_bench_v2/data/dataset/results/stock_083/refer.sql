WITH params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
trading_days AS (
    SELECT DISTINCT t.TRADINGDATE
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET IN (83, 90)
      AND YEAR(t.TRADINGDATE) = YEAR(p.as_of_date)
),
ranked_days AS (
    SELECT
        DATE_FORMAT(TRADINGDATE, '%Y-%m') AS month_label,
        TRADINGDATE AS rebalance_date,
        ROW_NUMBER() OVER (
            PARTITION BY YEAR(TRADINGDATE), MONTH(TRADINGDATE)
            ORDER BY TRADINGDATE DESC
        ) AS day_rank
    FROM trading_days
)
SELECT
    month_label AS `月份`,
    rebalance_date AS `调仓日期`
FROM ranked_days
WHERE day_rank = 4
ORDER BY rebalance_date;
