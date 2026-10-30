WITH params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
trade_asof AS (
    SELECT MAX(t.TRADINGDATE) AS trade_day
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET IN (83, 90)
),
index_prices AS (
    SELECT
        q.TRADINGDAY,
        q.CLOSEPRICE
    FROM qt_indexquote q
    JOIN secumain s
      ON s.INNERCODE = q.INNERCODE
    JOIN trade_asof ta
      ON q.TRADINGDAY <= ta.trade_day
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE = '000001'
      AND q.CLOSEPRICE IS NOT NULL
),
features AS (
    SELECT
        TRADINGDAY,
        CLOSEPRICE,
        CLOSEPRICE / LAG(CLOSEPRICE) OVER (ORDER BY TRADINGDAY) - 1.0 AS daily_return,
        CLOSEPRICE / LAG(CLOSEPRICE, 250) OVER (ORDER BY TRADINGDAY) - 1.0 AS return_250d
    FROM index_prices
),
ordered_prices AS (
    SELECT
        TRADINGDAY,
        CLOSEPRICE,
        return_250d,
        STDDEV_POP(daily_return) OVER (
            ORDER BY TRADINGDAY
            ROWS BETWEEN 249 PRECEDING AND CURRENT ROW
        ) AS volatility_250d,
        ROW_NUMBER() OVER (ORDER BY TRADINGDAY DESC) AS rn_desc
    FROM features
),
corr_sample AS (
    SELECT
        TRADINGDAY,
        CLOSEPRICE,
        volatility_250d
    FROM ordered_prices
    WHERE volatility_250d IS NOT NULL
      AND rn_desc <= 250
),
latest_metric AS (
    SELECT TRADINGDAY, volatility_250d, return_250d
    FROM ordered_prices
    WHERE rn_desc = 1
),
corr_stats AS (
    SELECT
        COUNT(*) AS n,
        SUM(CLOSEPRICE) AS sum_x,
        SUM(volatility_250d) AS sum_y,
        SUM(CLOSEPRICE * volatility_250d) AS sum_xy,
        SUM(CLOSEPRICE * CLOSEPRICE) AS sum_x2,
        SUM(volatility_250d * volatility_250d) AS sum_y2
    FROM corr_sample
)
SELECT
    l.TRADINGDAY AS `最新交易日`,
    ROUND(l.volatility_250d, 4) AS `250日波动率`,
    ROUND(l.return_250d * 100.0, 2) AS `近一年指数涨跌幅(%)`,
    ROUND(
        CASE
            WHEN (c.n * c.sum_x2 - POW(c.sum_x, 2)) > 0
             AND (c.n * c.sum_y2 - POW(c.sum_y, 2)) > 0 THEN
                (c.n * c.sum_xy - c.sum_x * c.sum_y) /
                SQRT((c.n * c.sum_x2 - POW(c.sum_x, 2)) * (c.n * c.sum_y2 - POW(c.sum_y, 2)))
            ELSE NULL
        END,
        4
    ) AS `价格-波动率相关系数`
FROM latest_metric l
CROSS JOIN corr_stats c;
