WITH params AS (
    SELECT
        DATE('2025-01-02') AS start_date,
        DATE('2025-12-31') AS end_date
),
target_stocks AS (
    SELECT
        s.INNERCODE,
        s.SECUCODE
    FROM secumain s
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET = 90
      AND s.SECUCODE IN ('000063', '000333', '002595')
),
stock_prices AS (
    SELECT
        s.SECUCODE,
        MAX(CASE WHEN p.TRADINGDAY = d.start_date THEN p.BACKWARDPRICE END) AS start_price,
        MAX(CASE WHEN p.TRADINGDAY = d.end_date THEN p.BACKWARDPRICE END) AS end_price,
        COUNT(DISTINCT p.TRADINGDAY) AS price_count
    FROM target_stocks s
    CROSS JOIN params d
    JOIN qt_performancedata p
      ON p.INNERCODE = s.INNERCODE
     AND p.TRADINGDAY IN (d.start_date, d.end_date)
    GROUP BY s.SECUCODE
),
stock_returns AS (
    SELECT
        SECUCODE,
        end_price / NULLIF(start_price, 0) - 1 AS stock_return
    FROM stock_prices
    WHERE price_count = 2
      AND start_price > 0
      AND end_price > 0
),
portfolio AS (
    SELECT
        COUNT(*) AS stock_count,
        AVG(stock_return) AS portfolio_return
    FROM stock_returns
),
benchmark_prices AS (
    SELECT
        MAX(CASE WHEN q.TRADINGDAY = d.start_date THEN q.CLOSEPRICE END) AS start_level,
        MAX(CASE WHEN q.TRADINGDAY = d.end_date THEN q.CLOSEPRICE END) AS end_level,
        COUNT(DISTINCT q.TRADINGDAY) AS price_count
    FROM secumain s
    CROSS JOIN params d
    JOIN qt_indexquote q
      ON q.INNERCODE = s.INNERCODE
     AND q.TRADINGDAY IN (d.start_date, d.end_date)
    WHERE s.SECUCATEGORY = 4
      AND s.SECUMARKET = 84
      AND s.SECUCODE = 'H00906'
),
benchmark AS (
    SELECT
        end_level / NULLIF(start_level, 0) - 1 AS csi800_total_return
    FROM benchmark_prices
    WHERE price_count = 2
      AND start_level > 0
      AND end_level > 0
)
SELECT
    ROUND(p.portfolio_return, 6) AS portfolio_return,
    ROUND(b.csi800_total_return, 6) AS csi800_total_return,
    ROUND(p.portfolio_return - b.csi800_total_return, 6) AS excess_return
FROM portfolio p
CROSS JOIN benchmark b
WHERE p.stock_count = 3;
