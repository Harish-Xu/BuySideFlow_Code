WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_dt
),
target_index AS (
    SELECT
        s.INNERCODE,
        CASE s.SECUCODE WHEN '000001' THEN '上证综指' WHEN '399006' THEN '创业板指' END AS index_name,  -- 用题面名称
        s.SECUCODE
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE IN ('399006', '000001')
),
quote_ranked AS (
    SELECT
        ti.INNERCODE,
        ti.index_name,
        ti.SECUCODE,
        q.TRADINGDAY,
        q.CLOSEPRICE,
        q.TURNOVERVALUE,
        ROW_NUMBER() OVER (
            PARTITION BY ti.INNERCODE
            ORDER BY q.TRADINGDAY DESC
        ) AS rn_desc
    FROM target_index ti
    JOIN qt_indexquote q
      ON q.INNERCODE = ti.INNERCODE
    JOIN params p
      ON q.TRADINGDAY < p.as_of_dt
),
window_60 AS (
    SELECT
        *
    FROM quote_ranked
    WHERE rn_desc <= 60
)
SELECT
    index_name AS `指数`,
    ROUND(MIN(CLOSEPRICE), 2) AS `60日低点`,
    ROUND(MAX(CASE WHEN rn_desc = 1 THEN CLOSEPRICE END), 2) AS `最新收盘价`,
    ROUND(
        (MAX(CASE WHEN rn_desc = 1 THEN CLOSEPRICE END) - MIN(CLOSEPRICE)) /
        NULLIF(MIN(CLOSEPRICE), 0) * 100,
        2
    ) AS `反弹幅度(%)`,
    ROUND(
        (MAX(CASE WHEN rn_desc = 1 THEN CLOSEPRICE END) /
         NULLIF(MAX(CASE WHEN rn_desc = 11 THEN CLOSEPRICE END), 0) - 1) * 100,
        2
    ) AS `近10日涨幅(%)`,
    ROUND(
        AVG(CASE WHEN rn_desc <= 5 THEN TURNOVERVALUE END) /
        NULLIF(AVG(TURNOVERVALUE), 0),
        4
    ) AS `成交额放大比例`
FROM window_60
GROUP BY INNERCODE, index_name, SECUCODE
ORDER BY SECUCODE ASC;
