-- name: all_index_history
WITH sw_level1_index AS (
    SELECT DISTINCT
        sm.INNERCODE,
        sm.SECUCODE,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, sm.SECUCODE) AS index_name,
        'industry' AS index_type
    FROM lc_indexbasicinfo ibi
    JOIN secumain sm
      ON sm.INNERCODE = ibi.INDEXCODE
    WHERE sm.SECUCATEGORY = 4
      AND ibi.INDUSTRYSTANDARD = 38
      AND ibi.PUBINDEXTYPE = 121101
),
benchmark_index AS (
    SELECT
        s.INNERCODE,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS index_name,
        'benchmark' AS index_type
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE = '${benchmark_code}'
    ORDER BY s.INNERCODE
    LIMIT 1
),
all_index AS (
    SELECT * FROM sw_level1_index
    UNION ALL
    SELECT * FROM benchmark_index
)
SELECT
    ai.index_type,
    ai.SECUCODE,
    ai.index_name,
    q.TRADINGDAY AS trade_date,
    q.CLOSEPRICE AS close_price,
    q.TURNOVERVALUE AS turnover_value
FROM all_index ai
JOIN qt_indexquote q
  ON q.INNERCODE = ai.INNERCODE
WHERE q.TRADINGDAY BETWEEN '${start_date}' AND '${end_date}'
ORDER BY ai.index_type, ai.SECUCODE, q.TRADINGDAY;

-- name: trading_calendar
SELECT DISTINCT
    DATE(t.TRADINGDATE) AS trade_date
FROM qt_tradingdaynew t
WHERE t.TRADINGDATE BETWEEN '${start_date}' AND '${end_date}'
  AND t.IFTRADINGDAY = 1
  AND t.SECUMARKET IN (83, 90)
ORDER BY trade_date;
