WITH params AS (
    SELECT DATE('2025-08-15') AS as_of_date
),
day_quotes AS (
    SELECT
        q.INNERCODE,
        DATE(q.TRADINGDAY) AS trading_day,
        q.CLOSEPRICE,
        q.DISCOUNTRATIO,
        ROW_NUMBER() OVER (
            PARTITION BY q.INNERCODE, DATE(q.TRADINGDAY)
            ORDER BY q.INSERTTIME DESC, q.ID DESC
        ) AS rn
    FROM qt_fundsperformancehis q
    CROSS JOIN params p
    WHERE q.TRADINGDAY >= p.as_of_date
      AND q.TRADINGDAY < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND q.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND q.DISCOUNTRATIO IS NOT NULL
      AND q.CLOSEPRICE IS NOT NULL
)
SELECT
    sm.SECUCODE AS `基金代码`,
    sm.SECUABBR AS `基金简称`,
    dq.trading_day AS `交易日`,
    dq.CLOSEPRICE AS `收盘价(元)`,
    ROUND(dq.DISCOUNTRATIO, 2) AS `折价率(%)`
FROM day_quotes dq
JOIN secumain sm
    ON sm.INNERCODE = dq.INNERCODE
WHERE dq.rn = 1
  AND dq.DISCOUNTRATIO > 0
ORDER BY dq.DISCOUNTRATIO DESC, sm.SECUCODE ASC
LIMIT 10;
