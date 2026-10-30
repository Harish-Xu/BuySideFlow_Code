WITH params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
latest_trade AS (
    SELECT MAX(t.TRADINGDATE) AS trading_day
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET = 83
),
trade_days AS (
    SELECT TRADINGDATE
    FROM (
        SELECT
            t.TRADINGDATE,
            ROW_NUMBER() OVER (ORDER BY t.TRADINGDATE DESC) AS rn_desc
        FROM qt_tradingdaynew t
        JOIN latest_trade lt
          ON t.TRADINGDATE <= lt.trading_day
        WHERE t.IFTRADINGDAY = 1
          AND t.SECUMARKET = 83
    ) d
    WHERE rn_desc <= 560
),
industry_info AS (
    SELECT
        libi.INDEXCODE AS innercode,
        sm.SECUCODE AS industry_code,
        REPLACE(sm.SECUABBR, '申万', '') AS industry_name
    FROM lc_indexbasicinfo libi
    JOIN secumain sm
      ON sm.INNERCODE = libi.INDEXCODE
    WHERE sm.SECUCATEGORY = 4
      AND libi.PUBINDEXTYPE = 121101
      AND libi.INDUSTRYSTANDARD = 38
)
SELECT
    ii.industry_code,
    ii.industry_name,
    q.TRADINGDAY AS trading_day,
    q.CLOSEPRICE AS close_price,
    q.TURNOVERVALUE / NULLIF(q.NEGOTIABLEMV, 0) * 100 AS turnover_rate
FROM industry_info ii
JOIN qt_indexquote q
  ON q.INNERCODE = ii.innercode
JOIN trade_days td
  ON td.TRADINGDATE = q.TRADINGDAY
WHERE q.CLOSEPRICE IS NOT NULL
  AND q.CLOSEPRICE > 0
  AND q.TURNOVERVALUE IS NOT NULL
  AND q.NEGOTIABLEMV IS NOT NULL
  AND q.NEGOTIABLEMV > 0
ORDER BY ii.industry_code, q.TRADINGDAY;
