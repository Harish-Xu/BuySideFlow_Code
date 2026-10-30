WITH target AS (
    SELECT
        bc.INNERCODE,
        bc.SECUCODE,
        bc.SECUABBR
    FROM bond_code bc
    WHERE bc.SECUCODE = '123049'
      AND bc.BONDTYPELEVEL1DESC = '可转债'
      AND bc.BONDTYPELEVEL2DESC = '可转债'
),
valid_quotes AS (
    SELECT
        t.SECUCODE,
        t.SECUABBR,
        q.TRADINGDAY,
        q.CLOSEPRICE,
        q.STOCKPRICE,
        q.CONVERTPREMIUMRATE
    FROM target t
    JOIN bond_conbdexchangequote q FORCE INDEX (IX_BOND_CONBDEXCHANGEQUOTE)
      ON t.INNERCODE = q.INNERCODE
    WHERE q.TRADINGDAY BETWEEN '2024-01-01' AND '2024-12-31'
      AND q.CLOSEPRICE IS NOT NULL
      AND q.CLOSEPRICE > 0
      AND q.STOCKPRICE IS NOT NULL
      AND q.STOCKPRICE > 0
      AND q.CONVERTPREMIUMRATE IS NOT NULL
),
boundary_days AS (
    SELECT
        MIN(TRADINGDAY) AS start_day,
        MAX(TRADINGDAY) AS end_day
    FROM valid_quotes
),
boundary_quotes AS (
    SELECT
        MAX(v.SECUCODE) AS secu_code,
        MAX(v.SECUABBR) AS secu_abbr,
        MAX(CASE WHEN v.TRADINGDAY = d.start_day THEN v.TRADINGDAY END) AS start_day,
        MAX(CASE WHEN v.TRADINGDAY = d.end_day THEN v.TRADINGDAY END) AS end_day,
        MAX(CASE WHEN v.TRADINGDAY = d.start_day THEN v.CLOSEPRICE END) AS start_close_price,
        MAX(CASE WHEN v.TRADINGDAY = d.end_day THEN v.CLOSEPRICE END) AS end_close_price,
        MAX(CASE WHEN v.TRADINGDAY = d.start_day THEN v.STOCKPRICE END) AS start_stock_price,
        MAX(CASE WHEN v.TRADINGDAY = d.end_day THEN v.STOCKPRICE END) AS end_stock_price,
        MAX(CASE WHEN v.TRADINGDAY = d.start_day THEN v.CONVERTPREMIUMRATE END) AS start_premium_rate,
        MAX(CASE WHEN v.TRADINGDAY = d.end_day THEN v.CONVERTPREMIUMRATE END) AS end_premium_rate
    FROM valid_quotes v
    CROSS JOIN boundary_days d
)
SELECT
    secu_code AS `证券代码`,
    secu_abbr AS `转债简称`,
    DATE(start_day) AS `区间起始日`,
    DATE(end_day) AS `区间结束日`,
    ROUND(start_close_price, 2) AS `期初收盘价`,
    ROUND(end_close_price, 2) AS `期末收盘价`,
    ROUND((end_close_price / start_close_price - 1) * 100, 2) AS `转债涨幅`,
    ROUND((end_stock_price / start_stock_price - 1) * 100, 2) AS `正股涨幅`,
    ROUND(end_premium_rate - start_premium_rate, 2) AS `转股溢价率变动`
FROM boundary_quotes
WHERE start_day IS NOT NULL
  AND end_day IS NOT NULL
  AND start_close_price > 0
  AND start_stock_price > 0;
