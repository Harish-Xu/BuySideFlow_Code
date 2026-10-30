WITH curve_ranked AS (
    SELECT
        DATE(ENDDATE) AS obs_date,
        YEARSTOMATURITY,
        YIELD,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(ENDDATE), YEARSTOMATURITY
            ORDER BY UPDATETIME DESC, ID DESC
        ) AS rn
    FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE CURVECODE = 10
      AND YIELDTYPECODE = 1
      AND YEARSTOMATURITY IN (3, 10)
      AND YIELD IS NOT NULL
      AND ENDDATE >= '2022-01-01'
      AND ENDDATE < '2025-01-01'
),
curve_daily AS (
    SELECT
        obs_date,
        YEARSTOMATURITY,
        YIELD
    FROM curve_ranked
    WHERE rn = 1
),
spread_series AS (
    SELECT
        y10.obs_date,
        y10.YIELD - y3.YIELD AS series_value
    FROM curve_daily y10
    JOIN curve_daily y3
      ON y10.obs_date = y3.obs_date
    WHERE y10.YEARSTOMATURITY = 10
      AND y3.YEARSTOMATURITY = 3
),
index_quote_ranked AS (
    SELECT
        DATE(q.TRADINGDAY) AS obs_date,
        q.CLOSEPRICE AS series_value,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(q.TRADINGDAY)
            ORDER BY q.UPDATETIME DESC, q.ID DESC
        ) AS rn
    FROM bond_chinabondindexquote q
    JOIN bond_indexbasicinfo i
      ON q.INNERCODE = i.INDEXCODE
    WHERE i.CHINAME = '中债-国债总指数_7-10年'
      AND q.TRADINGDAY >= '2023-01-01'
      AND q.TRADINGDAY < '2025-04-01'
      AND q.CLOSEPRICE IS NOT NULL
      AND q.CLOSEPRICE > 0
)
SELECT
    'spread_10y_3y' AS series_name,
    obs_date,
    series_value
FROM spread_series
UNION ALL
SELECT
    'index_price' AS series_name,
    obs_date,
    series_value
FROM index_quote_ranked
WHERE rn = 1
ORDER BY series_name, obs_date;
