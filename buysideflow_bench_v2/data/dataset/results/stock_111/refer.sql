WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
index_info AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '000300'
),
trade_days AS (
    SELECT
        qi.TRADINGDAY,
        qi.CLOSEPRICE,
        ROW_NUMBER() OVER (ORDER BY qi.TRADINGDAY DESC) AS rn_desc
    FROM qt_indexquote qi
    JOIN index_info ii
      ON ii.INNERCODE = qi.INNERCODE
    JOIN params p
      ON qi.TRADINGDAY <= p.as_of_date
    WHERE qi.CLOSEPRICE > 0
),
window_200d AS (
    SELECT
        TRADINGDAY,
        CLOSEPRICE
    FROM trade_days
    WHERE rn_desc <= 200
),
ordered_data AS (
    SELECT
        TRADINGDAY,
        CLOSEPRICE
    FROM window_200d
),
price_with_ma AS (
    SELECT
        TRADINGDAY,
        CLOSEPRICE,
        AVG(CLOSEPRICE) OVER (
            ORDER BY TRADINGDAY
            ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
        ) AS ma60,
        COUNT(CLOSEPRICE) OVER (
            ORDER BY TRADINGDAY
            ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
        ) AS ma60_count
    FROM ordered_data
),
bias_data AS (
    SELECT
        TRADINGDAY,
        CLOSEPRICE,
        CLOSEPRICE / NULLIF(ma60, 0) - 1 AS bias
    FROM price_with_ma
    WHERE ma60_count = 60
      AND ma60 > 0
),
biasma_data AS (
    SELECT
        TRADINGDAY,
        CLOSEPRICE,
        bias,
        AVG(bias) OVER (
            ORDER BY TRADINGDAY
            ROWS BETWEEN 9 PRECEDING AND CURRENT ROW
        ) AS biasma,
        COUNT(bias) OVER (
            ORDER BY TRADINGDAY
            ROWS BETWEEN 9 PRECEDING AND CURRENT ROW
        ) AS biasma_count
    FROM bias_data
),
biasma_valid AS (
    SELECT
        TRADINGDAY,
        CLOSEPRICE,
        bias,
        biasma
    FROM biasma_data
    WHERE biasma_count = 10
),
biasma_stats AS (
    SELECT
        TRADINGDAY,
        CLOSEPRICE,
        bias,
        biasma,
        AVG(biasma) OVER (
            ORDER BY TRADINGDAY
            ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
        ) AS biasma_mean,
        STDDEV_POP(biasma) OVER (
            ORDER BY TRADINGDAY
            ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
        ) AS biasma_std,
        COUNT(biasma) OVER (
            ORDER BY TRADINGDAY
            ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
        ) AS zscore_count
    FROM biasma_valid
),
final_calc AS (
    SELECT
        TRADINGDAY,
        CLOSEPRICE,
        bias,
        biasma,
        CASE
            WHEN biasma_std > 0 THEN (biasma - biasma_mean) / biasma_std
        END AS z_score
    FROM biasma_stats
    WHERE zscore_count = 30
)
SELECT
    DATE(TRADINGDAY) AS `交易日`,
    ROUND(CLOSEPRICE, 4) AS `沪深300收盘价`,
    ROUND(bias * 100, 4) AS `乖离率(%)`,
    ROUND(biasma * 100, 4) AS `乖离率10日均值(%)`,
    ROUND(z_score, 4) AS `最近30日标准化得分`,
    CASE
        WHEN z_score >= 1.5 THEN 'SELL'
        WHEN z_score <= -1.5 THEN 'BUY'
        ELSE 'HOLD'
    END AS `交易信号`
FROM final_calc
WHERE z_score IS NOT NULL
ORDER BY TRADINGDAY DESC
LIMIT 1;
