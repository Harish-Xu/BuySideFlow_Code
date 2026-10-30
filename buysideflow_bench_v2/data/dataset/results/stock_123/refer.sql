WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
stock_info AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCATEGORY = 1
      AND SECUMARKET = 83
      AND SECUCODE = '600519'
    ORDER BY INNERCODE
    LIMIT 1
),
week_end_days AS (
    SELECT trading_date
    FROM (
        SELECT
            td.TRADINGDATE AS trading_date,
            ROW_NUMBER() OVER (
                PARTITION BY YEARWEEK(td.TRADINGDATE, 1)
                ORDER BY td.TRADINGDATE DESC
            ) AS rn_week
        FROM qt_tradingdaynew td
        JOIN params p
          ON td.TRADINGDATE <= p.as_of_date
        WHERE td.IFTRADINGDAY = 1
          AND td.SECUMARKET = 83
          AND YEAR(td.TRADINGDATE) = YEAR(p.as_of_date)
    ) t
    WHERE rn_week = 1
),
quote_base AS (
    SELECT
        q.TRADINGDAY,
        q.OPENPRICE,
        q.HIGHPRICE,
        q.LOWPRICE,
        q.CLOSEPRICE,
        q.PREVCLOSEPRICE
    FROM qt_dailyquote q
    JOIN stock_info si
      ON si.INNERCODE = q.INNERCODE
    WHERE q.OPENPRICE IS NOT NULL
      AND q.HIGHPRICE IS NOT NULL
      AND q.LOWPRICE IS NOT NULL
      AND q.CLOSEPRICE IS NOT NULL
      AND q.PREVCLOSEPRICE IS NOT NULL
      AND q.HIGHPRICE >= q.LOWPRICE
),
window_quotes AS (
    SELECT
        w.trading_date AS calc_date,
        td.TRADINGDATE AS TRADINGDAY,
        q.OPENPRICE,
        q.HIGHPRICE,
        q.LOWPRICE,
        q.PREVCLOSEPRICE,
        ROW_NUMBER() OVER (
            PARTITION BY w.trading_date
            ORDER BY td.TRADINGDATE DESC
        ) AS rn_desc
    FROM week_end_days w
    JOIN qt_tradingdaynew td
      ON td.TRADINGDATE <= w.trading_date
     AND td.SECUMARKET = 83
     AND td.IFTRADINGDAY = 1
    LEFT JOIN quote_base q
      ON q.TRADINGDAY = td.TRADINGDATE
),
last_22d AS (
    SELECT
        calc_date,
        OPENPRICE,
        HIGHPRICE,
        LOWPRICE,
        PREVCLOSEPRICE
    FROM window_quotes
    WHERE rn_desc <= 22
),
arbr_result AS (
    SELECT
        calc_date,
        CASE
            WHEN COUNT(OPENPRICE) = 22 AND SUM(OPENPRICE - LOWPRICE) > 0
            THEN SUM(HIGHPRICE - OPENPRICE) / SUM(OPENPRICE - LOWPRICE) * 100
            ELSE NULL
        END AS ar_value,
        CASE
            WHEN COUNT(OPENPRICE) = 22 AND SUM(GREATEST(PREVCLOSEPRICE - LOWPRICE, 0)) > 0
            THEN SUM(GREATEST(HIGHPRICE - PREVCLOSEPRICE, 0)) /
                 SUM(GREATEST(PREVCLOSEPRICE - LOWPRICE, 0)) * 100
            ELSE NULL
        END AS br_value,
        COUNT(*) AS obs_cnt
    FROM last_22d
    GROUP BY calc_date
)
SELECT
    calc_date AS `计算日期`,
    ROUND(ar_value, 4) AS `AR`,
    ROUND(br_value, 4) AS `BR`
FROM arbr_result
WHERE obs_cnt = 22
ORDER BY calc_date;
