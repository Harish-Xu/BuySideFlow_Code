WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
target_etfs AS (
    SELECT '510300' AS secu_code
    UNION ALL SELECT '513100'
    UNION ALL SELECT '518880'
),
etf_ranked AS (
    SELECT
        sm.INNERCODE AS inner_code,
        sm.SECUCODE AS secu_code,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, sm.SECUCODE) AS etf_name,
        ROW_NUMBER() OVER (
            PARTITION BY sm.SECUCODE
            ORDER BY
                CASE WHEN sm.SECUCATEGORY = 8 THEN 1 ELSE 9 END,
                sm.INNERCODE
        ) AS rn
    FROM secumain sm
    JOIN target_etfs t
      ON t.secu_code = sm.SECUCODE
    JOIN params p
    WHERE sm.SECUCATEGORY = 8
      AND (sm.LISTEDDATE IS NULL OR DATE(sm.LISTEDDATE) <= p.as_of_date)
),
etf_info AS (
    SELECT inner_code, secu_code, etf_name
    FROM etf_ranked
    WHERE rn = 1
),
valid_quotes AS (
    SELECT
        e.secu_code,
        e.etf_name,
        DATE(q.TRADINGDAY) AS trading_day,
        q.CLOSEPRICE AS close_price
    FROM etf_info e
    JOIN qt_dailyquote q
      ON q.INNERCODE = e.inner_code
    JOIN params p
      ON DATE(q.TRADINGDAY) <= p.as_of_date
    WHERE q.CLOSEPRICE > 0
),
common_days AS (
    SELECT trading_day
    FROM valid_quotes
    GROUP BY trading_day
    HAVING COUNT(DISTINCT secu_code) = 3
),
recent_common_days AS (
    SELECT trading_day
    FROM (
        SELECT
            trading_day,
            ROW_NUMBER() OVER (ORDER BY trading_day DESC) AS rn_desc
        FROM common_days
    ) d
    WHERE rn_desc <= 500
)
SELECT
    v.secu_code AS `ETF代码`,
    v.etf_name AS `ETF名称`,
    v.trading_day AS `交易日`,
    v.close_price AS `收盘价`
FROM valid_quotes v
JOIN recent_common_days d
  ON d.trading_day = v.trading_day
ORDER BY v.trading_day, v.secu_code;
