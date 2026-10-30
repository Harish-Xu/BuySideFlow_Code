WITH target_index AS (
    SELECT
        INDEXCODE,
        SECUCODE,
        COALESCE(CHINAME, SECUABBR) AS index_name
    FROM bond_indexbasicinfo
    WHERE SECUCODE IN ('CBA00601', 'CBA04401')
)
SELECT
    t.SECUCODE AS index_code,
    t.index_name,
    q.TRADINGDAY AS trade_date,
    q.CLOSEPRICE AS close_price
FROM target_index t
JOIN bond_chinabondindexquote q
  ON q.INNERCODE = t.INDEXCODE
WHERE q.TRADINGDAY >= '2024-12-31'
  AND q.TRADINGDAY <= '2025-12-22'
  AND q.CLOSEPRICE IS NOT NULL
  AND q.CLOSEPRICE > 0
ORDER BY t.SECUCODE, q.TRADINGDAY;
