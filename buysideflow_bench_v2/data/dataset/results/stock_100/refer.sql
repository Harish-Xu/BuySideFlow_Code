-- name: all_index_history
WITH sw_level1_index AS (
    SELECT
        sm.INNERCODE,
        sm.SECUCODE,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, sm.SECUCODE) AS index_name,
        'industry' AS index_type
    FROM secumain sm
    WHERE sm.SECUCATEGORY = 4
      AND EXISTS (
          SELECT 1
          FROM lc_indexbasicinfo ibi
          WHERE ibi.INDEXCODE = sm.INNERCODE
            AND ibi.INDUSTRYSTANDARD = 38
            AND ibi.PUBINDEXTYPE = 121101
      )
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
), calendar AS (
    SELECT TRADINGDATE FROM qt_tradingdaynew
    WHERE SECUMARKET=83 AND IFTRADINGDAY=1
      AND TRADINGDATE BETWEEN '${start_date}' AND '${end_date}'
)
SELECT
    ai.index_type,
    ai.SECUCODE,
    ai.index_name,
    c.TRADINGDATE AS trade_date,
    CASE WHEN q.CLOSEPRICE>0 THEN q.CLOSEPRICE END AS close_price
FROM all_index ai
CROSS JOIN calendar c
LEFT JOIN qt_indexquote q ON q.INNERCODE=ai.INNERCODE AND q.TRADINGDAY=c.TRADINGDATE
ORDER BY ai.index_type, ai.SECUCODE, c.TRADINGDATE;
