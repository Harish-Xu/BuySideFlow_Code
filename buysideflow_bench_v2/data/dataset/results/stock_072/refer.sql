WITH params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
target_index AS (
    SELECT s.INNERCODE
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE = '000300'
    ORDER BY s.INNERCODE
    LIMIT 1
),
recent_prices AS (
    SELECT
        q.TRADINGDAY,
        q.HIGHPRICE,
        q.LOWPRICE
    FROM qt_indexquote q
    JOIN target_index i
      ON i.INNERCODE = q.INNERCODE
    JOIN params p
      ON q.TRADINGDAY <= p.as_of_date
    WHERE q.HIGHPRICE IS NOT NULL
      AND q.LOWPRICE IS NOT NULL
      AND q.HIGHPRICE > 0
      AND q.LOWPRICE > 0
      AND q.HIGHPRICE >= q.LOWPRICE
    ORDER BY q.TRADINGDAY DESC
    LIMIT 617
),
ordered_prices AS (
    SELECT
        TRADINGDAY,
        HIGHPRICE,
        LOWPRICE,
        ROW_NUMBER() OVER (ORDER BY TRADINGDAY) AS seq
    FROM recent_prices
),
slopes AS (
    SELECT
        p1.TRADINGDAY,
        (
            18 * SUM(p2.HIGHPRICE * p2.LOWPRICE)
            - SUM(p2.HIGHPRICE) * SUM(p2.LOWPRICE)
        ) / NULLIF(
            18 * SUM(p2.LOWPRICE * p2.LOWPRICE)
            - POWER(SUM(p2.LOWPRICE), 2),
            0
        ) AS slope_18d
    FROM ordered_prices p1
    JOIN ordered_prices p2
      ON CAST(p2.seq AS SIGNED)
         BETWEEN CAST(p1.seq AS SIGNED) - 17 AND CAST(p1.seq AS SIGNED)
    GROUP BY p1.TRADINGDAY, p1.seq
    HAVING COUNT(*) = 18
),
slope_stats AS (
    SELECT
        AVG(slope_18d) AS slope_mean_600d,
        STDDEV_SAMP(slope_18d) AS slope_std_600d
    FROM slopes
),
latest_slope AS (
    SELECT TRADINGDAY, slope_18d
    FROM slopes
    ORDER BY TRADINGDAY DESC
    LIMIT 1
),
signal_value AS (
    SELECT
        l.TRADINGDAY,
        l.slope_18d,
        s.slope_mean_600d,
        s.slope_std_600d,
        (l.slope_18d - s.slope_mean_600d) / NULLIF(s.slope_std_600d, 0) AS zscore
    FROM latest_slope l
    CROSS JOIN slope_stats s
)
SELECT
    TRADINGDAY AS trade_date,
    ROUND(slope_18d, 6) AS rsrs_slope_18d,
    ROUND(slope_mean_600d, 6) AS slope_mean_600d,
    ROUND(slope_std_600d, 6) AS slope_std_600d,
    ROUND(zscore, 4) AS rsrs_zscore,
    CASE
        WHEN zscore > 0.7 THEN 'BUY'
        WHEN zscore < -0.7 THEN 'SELL'
        ELSE 'HOLD'
    END AS signal
FROM signal_value;
