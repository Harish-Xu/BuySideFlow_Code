WITH
params AS (
    SELECT TIMESTAMP('2025-08-20 23:59:59') AS as_of_dt
),
target_index AS (
    SELECT
        s.INNERCODE,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS index_name,
        CASE s.SECUCODE
            WHEN '000300' THEN 1
            WHEN '000905' THEN 2
            ELSE 9
        END AS sort_order
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE IN ('000300', '000905')
), calendar AS (
    SELECT TRADINGDATE FROM qt_tradingdaynew
    WHERE SECUMARKET=83 AND IFTRADINGDAY=1
      AND TRADINGDATE BETWEEN '2023-09-01' AND '2025-08-20'
)
SELECT
    ti.sort_order,
    ti.SECUCODE,
    ti.index_name,
    DATE(c.TRADINGDATE) AS TRADINGDAY,
    q.CLOSEPRICE,
    q.TURNOVERVALUE
FROM target_index ti
CROSS JOIN calendar c
LEFT JOIN qt_indexquote q ON q.INNERCODE=ti.INNERCODE AND q.TRADINGDAY=c.TRADINGDATE
ORDER BY ti.sort_order, c.TRADINGDATE;
