WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
latest_trade AS (
    SELECT MAX(TRADINGDATE) AS trading_day
    FROM qt_tradingdaynew
    WHERE IFTRADINGDAY = 1
      AND SECUMARKET = 83
      AND TRADINGDATE < (SELECT as_of_date FROM params)
),
trade_window AS (
    SELECT TRADINGDATE
    FROM (
        SELECT
            td.TRADINGDATE,
            ROW_NUMBER() OVER (ORDER BY td.TRADINGDATE DESC) AS rn_desc
        FROM qt_tradingdaynew td
        JOIN latest_trade lt
          ON td.TRADINGDATE <= lt.trading_day
        WHERE td.IFTRADINGDAY = 1
          AND td.SECUMARKET = 83
    ) t
    WHERE rn_desc <= 11
),
idx_map AS (
    SELECT INNERCODE, SECUCODE
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE IN ('000300', '000852')
),
component_snapshot AS (
    SELECT
        ic.INDEXINNERCODE,
        ic.SECUINNERCODE
    FROM lc_indexcomponent ic
    JOIN latest_trade lt
      ON ic.INDATE <= lt.trading_day
    WHERE ic.OUTDATE IS NULL OR ic.OUTDATE > lt.trading_day
),
valuation_snapshot AS (
    SELECT
        d.INNERCODE,
        d.TOTALMV
    FROM lc_dindicesforvaluation d
    JOIN latest_trade lt
      ON d.TRADINGDAY = lt.trading_day
    WHERE d.TOTALMV IS NOT NULL
),
hs300_large AS (
    SELECT cs.SECUINNERCODE AS INNERCODE
    FROM component_snapshot cs
    JOIN idx_map im
      ON im.INNERCODE = cs.INDEXINNERCODE
     AND im.SECUCODE = '000300'
    JOIN valuation_snapshot vs
      ON vs.INNERCODE = cs.SECUINNERCODE
    ORDER BY vs.TOTALMV DESC, cs.SECUINNERCODE
    LIMIT 20
),
zz1000_small AS (
    SELECT cs.SECUINNERCODE AS INNERCODE
    FROM component_snapshot cs
    JOIN idx_map im
      ON im.INNERCODE = cs.INDEXINNERCODE
     AND im.SECUCODE = '000852'
    JOIN valuation_snapshot vs
      ON vs.INNERCODE = cs.SECUINNERCODE
    ORDER BY vs.TOTALMV ASC, cs.SECUINNERCODE
    LIMIT 20
),
price_window AS (
    SELECT
        q.INNERCODE,
        q.TRADINGDAY,
        q.BACKWARDPRICE,
        ROW_NUMBER() OVER (
            PARTITION BY q.INNERCODE
            ORDER BY q.TRADINGDAY DESC
        ) AS rn_desc
    FROM qt_performancedata q
    JOIN trade_window tw
      ON tw.TRADINGDATE = q.TRADINGDAY
    WHERE q.BACKWARDPRICE IS NOT NULL
      AND q.BACKWARDPRICE > 0
),
stock_return_10d AS (
    SELECT
        INNERCODE,
        MAX(CASE WHEN rn_desc = 1 THEN BACKWARDPRICE END) /
        NULLIF(MAX(CASE WHEN rn_desc = 11 THEN BACKWARDPRICE END), 0) - 1 AS return_10d,
        COUNT(*) AS price_count
    FROM price_window
    GROUP BY INNERCODE
    HAVING COUNT(*) = 11
),
large_group AS (
    SELECT AVG(sr.return_10d) AS avg_return_10d, COUNT(*) AS sample_count
    FROM stock_return_10d sr
    JOIN hs300_large hg
      ON hg.INNERCODE = sr.INNERCODE
),
small_group AS (
    SELECT AVG(sr.return_10d) AS avg_return_10d, COUNT(*) AS sample_count
    FROM stock_return_10d sr
    JOIN zz1000_small sg
      ON sg.INNERCODE = sr.INNERCODE
)
SELECT
    lt.trading_day AS `最近计算日期`,
    CASE
        WHEN lg.sample_count < 20 OR sg.sample_count < 20 THEN '选择货币基金'
        WHEN lg.avg_return_10d > sg.avg_return_10d
         AND lg.avg_return_10d > 0 THEN '选择大盘股'
        WHEN sg.avg_return_10d > lg.avg_return_10d
         AND sg.avg_return_10d > 0 THEN '选择小盘股'
        ELSE '选择货币基金'
    END AS `信号`
FROM large_group lg
CROSS JOIN small_group sg
CROSS JOIN latest_trade lt;
