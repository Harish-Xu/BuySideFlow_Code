-- name: index_history
WITH target_index AS (
    SELECT
        s.INNERCODE,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS index_name
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE = '${index_code}'
    ORDER BY s.INNERCODE
    LIMIT 1
)
SELECT
    ti.SECUCODE,
    ti.index_name,
    q.TRADINGDAY AS trade_date,
    q.OPENPRICE AS open_price,
    q.HIGHPRICE AS high_price,
    q.LOWPRICE AS low_price,
    q.CLOSEPRICE AS close_price,
    q.TURNOVERVALUE AS turnover_value
FROM target_index ti
JOIN qt_indexquote q
  ON q.INNERCODE = ti.INNERCODE
WHERE q.TRADINGDAY BETWEEN '${start_date}' AND '${end_date}'
  AND q.CLOSEPRICE > 0
ORDER BY q.TRADINGDAY;
