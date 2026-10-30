WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
trade_days AS (
    SELECT TRADINGDATE
    FROM (
        SELECT
            td.TRADINGDATE,
            ROW_NUMBER() OVER (ORDER BY td.TRADINGDATE DESC) AS rn_desc
        FROM qt_tradingdaynew td
        JOIN params p
          ON td.TRADINGDATE <= p.as_of_date
        WHERE td.IFTRADINGDAY = 1
          AND td.SECUMARKET = 83
    ) t
    WHERE rn_desc <= 220
),
csi800 AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '000906'
    ORDER BY INNERCODE
    LIMIT 1
),
idx_window AS (
    SELECT
        iq.TRADINGDAY,
        iq.CLOSEPRICE,
        iq.HIGHPRICE,
        iq.LOWPRICE,
        ROW_NUMBER() OVER (ORDER BY iq.TRADINGDAY DESC) AS rn_desc
    FROM qt_indexquote iq
    JOIN csi800 c
      ON c.INNERCODE = iq.INNERCODE
    JOIN trade_days td
      ON td.TRADINGDATE = iq.TRADINGDAY
    WHERE iq.CLOSEPRICE IS NOT NULL
      AND iq.HIGHPRICE IS NOT NULL
      AND iq.LOWPRICE IS NOT NULL
),
latest_calc AS (
    SELECT
        MAX(CASE WHEN rn_desc = 1 THEN TRADINGDAY END) AS trading_day,
        (AVG(CASE WHEN rn_desc <= 5 THEN CLOSEPRICE END) - MIN(CLOSEPRICE)) /
        NULLIF(MAX(CLOSEPRICE) - MIN(CLOSEPRICE), 0) AS market_height,
        MAX(CASE WHEN rn_desc <= 60 THEN HIGHPRICE END) /
        NULLIF(MIN(CASE WHEN rn_desc <= 60 THEN LOWPRICE END), 0) AS recent_60d_high_low_ratio
    FROM idx_window
)
SELECT
    trading_day AS `交易日`,
    ROUND(market_height, 4) AS `市场高度`,
    ROUND(recent_60d_high_low_ratio, 4) AS `60日振幅比`,
    CASE
        WHEN market_height < 0.20 THEN '冷市'
        WHEN market_height > 0.90 OR recent_60d_high_low_ratio > 1.20 THEN '热市'
        ELSE '温市'
    END AS `市场温度信号`
FROM latest_calc;
