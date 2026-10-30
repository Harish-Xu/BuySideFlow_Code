WITH target_index AS (
    SELECT s.INNERCODE
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE = '000300'
    ORDER BY s.INNERCODE
    LIMIT 1
),
first_quote AS (
    SELECT MIN(q.TRADINGDAY) AS start_date
    FROM qt_indexquote q JOIN target_index i ON q.INNERCODE = i.INNERCODE
    WHERE q.TRADINGDAY <= '2025-12-31'
),
calendar AS (
    SELECT trade_date, LEAD(trade_date) OVER (ORDER BY trade_date) AS rebalance_date
    FROM (
        SELECT DISTINCT DATE(t.TRADINGDATE) AS trade_date
        FROM qt_tradingdaynew t CROSS JOIN first_quote f
        WHERE t.IFTRADINGDAY = 1 AND t.SECUMARKET IN (83, 90)
          AND t.TRADINGDATE >= f.start_date
          AND t.TRADINGDATE <= (
              SELECT MIN(TRADINGDATE) FROM qt_tradingdaynew
              WHERE IFTRADINGDAY = 1 AND SECUMARKET IN (83, 90)
                AND TRADINGDATE > '2025-12-31'
          )
    ) d
)
SELECT
    c.trade_date AS trading_day,
    c.rebalance_date,
    q.HIGHPRICE AS high_price,
    q.LOWPRICE AS low_price,
    q.CLOSEPRICE AS close_price
FROM calendar c CROSS JOIN target_index i
LEFT JOIN qt_indexquote q ON q.INNERCODE = i.INNERCODE AND q.TRADINGDAY = c.trade_date
WHERE c.trade_date <= '2025-12-31'
ORDER BY c.trade_date;
