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
recent_40_days AS (
    SELECT tradingdate
    FROM (
        SELECT DISTINCT t.TRADINGDATE AS tradingdate
        FROM qt_tradingdaynew t
        JOIN trade_asof ta
          ON t.TRADINGDATE <= ta.trade_day
        WHERE t.IFTRADINGDAY = 1
          AND t.SECUMARKET IN (83, 90)
        ORDER BY t.TRADINGDATE DESC
        LIMIT 40
    ) d
),
stock_returns AS (
    SELECT
        q.TRADINGDAY,
        q.CHANGEPCT / 100.0 AS stock_return
    FROM qt_performancedata q
    JOIN secumain s
      ON s.INNERCODE = q.INNERCODE
    JOIN recent_40_days d
      ON d.tradingdate = q.TRADINGDAY
    WHERE s.SECUCATEGORY = 1
      AND s.SECUCODE = '600519'
      AND q.CHANGEPCT IS NOT NULL
),
index_returns AS (
    SELECT
        q.TRADINGDAY,
        q.CHANGEPCT / 100.0 AS index_return
    FROM qt_indexquote q
    JOIN secumain s
      ON s.INNERCODE = q.INNERCODE
    JOIN recent_40_days d
      ON d.tradingdate = q.TRADINGDAY
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE = '399997'
      AND q.CHANGEPCT IS NOT NULL
),
aligned AS (
    SELECT
        s.TRADINGDAY,
        s.stock_return,
        i.index_return
    FROM stock_returns s
    JOIN index_returns i
      ON i.TRADINGDAY = s.TRADINGDAY
),
stats AS (
    SELECT
        MAX(TRADINGDAY) AS latest_trading_day,
        AVG(stock_return) AS avg_stock,
        AVG(index_return) AS avg_index
    FROM aligned
)
SELECT
    st.latest_trading_day AS `最近交易日`,
    ROUND(
        SUM((a.stock_return - st.avg_stock) * (a.index_return - st.avg_index)) /
        NULLIF(SUM(POW(a.index_return - st.avg_index, 2)), 0),
        6
    ) AS `Beta系数`
FROM aligned a
CROSS JOIN stats st;
