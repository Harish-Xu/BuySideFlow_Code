WITH
params AS (
    SELECT DATE('2024-12-30') AS as_of_dt
),
target_index AS (
    SELECT
        s.INNERCODE,
        COALESCE(s.CHINAME, s.SECUABBR, s.CHINAMEABBR, s.SECUCODE) AS index_name
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE IN ('000057', '000058')
)
SELECT
    ti.index_name,
    q.TRADINGDAY,
    q.CLOSEPRICE
FROM target_index ti
JOIN qt_indexquote q
  ON q.INNERCODE = ti.INNERCODE
JOIN params p
  ON q.TRADINGDAY <= p.as_of_dt
WHERE q.CLOSEPRICE > 0
ORDER BY ti.index_name, q.TRADINGDAY;
