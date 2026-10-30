WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
assets AS (
    SELECT
        s.INNERCODE,
        s.SECUCODE,
        CASE
            WHEN s.SECUCODE = '510300' THEN 'A股资产'
            WHEN s.SECUCODE = '513600' THEN '港股资产'
            WHEN s.SECUCODE = '513500' THEN '美股资产'
        END AS asset_type,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS asset_name
    FROM secumain s
    WHERE s.SECUCODE IN ('510300', '513600', '513500')
      AND s.SECUCATEGORY = 8
),
ranked_quotes AS (
    SELECT
        a.asset_type,
        a.SECUCODE,
        a.asset_name,
        a.INNERCODE,
        q.TRADINGDAY,
        q.CLOSEPRICE,
        ROW_NUMBER() OVER (
            PARTITION BY a.INNERCODE
            ORDER BY q.TRADINGDAY DESC
        ) AS rn_desc
    FROM assets a
    JOIN qt_dailyquote q
      ON q.INNERCODE = a.INNERCODE
    JOIN params p
      ON q.TRADINGDAY <= p.as_of_date
    WHERE q.CLOSEPRICE > 0
),
window_121d AS (
    SELECT
        asset_type,
        SECUCODE,
        asset_name,
        INNERCODE,
        TRADINGDAY,
        CLOSEPRICE
    FROM ranked_quotes
    WHERE rn_desc <= 121
),
daily_returns AS (
    SELECT
        w.asset_type,
        w.SECUCODE,
        w.asset_name,
        w.TRADINGDAY,
        w.CLOSEPRICE / LAG(w.CLOSEPRICE) OVER (
            PARTITION BY w.INNERCODE
            ORDER BY w.TRADINGDAY
        ) - 1 AS daily_return
    FROM window_121d w
),
asset_volatility AS (
    SELECT
        asset_type,
        SECUCODE,
        asset_name,
        COUNT(daily_return) AS return_count,
        STDDEV_SAMP(daily_return) AS vol_120d
    FROM daily_returns
    WHERE daily_return IS NOT NULL
    GROUP BY asset_type, SECUCODE, asset_name
    HAVING COUNT(daily_return) = 120
),
asset_weights AS (
    SELECT
        *,
        1 / vol_120d AS inv_vol,
        SUM(1 / vol_120d) OVER () AS total_inv
    FROM asset_volatility
    WHERE vol_120d > 0
)
SELECT
    asset_type,
    SECUCODE AS etf_code,
    asset_name,
    ROUND(vol_120d, 6) AS vol_120d,
    ROUND(inv_vol / total_inv, 6) AS final_weight
FROM asset_weights
ORDER BY FIELD(asset_type, 'A股资产', '港股资产', '美股资产');
