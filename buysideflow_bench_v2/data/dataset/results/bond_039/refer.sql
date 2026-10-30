WITH curve_ranked AS (
    SELECT
        DATE(ENDDATE) AS obs_date,
        YEARSTOMATURITY,
        CURVECODE,
        YIELD,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(ENDDATE), CURVECODE, YEARSTOMATURITY
            ORDER BY UPDATETIME DESC, ID DESC
        ) AS rn
    FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE CURVECODE IN (10, 199)
      AND YIELDTYPECODE = 1
      AND YEARSTOMATURITY IN (3, 5, 10)
      AND YIELD IS NOT NULL
      AND ENDDATE >= '2022-12-01'
      AND ENDDATE < '2025-01-01'
),
base AS (
    SELECT
        obs_date,
        YEARSTOMATURITY,
        CURVECODE,
        YIELD
    FROM curve_ranked
    WHERE rn = 1
),
daily_spread AS (
    SELECT
        l.obs_date,
        l.YEARSTOMATURITY,
        (l.YIELD - g.YIELD) * 10000 AS spread_bp
    FROM base l
    JOIN base g
      ON g.obs_date = l.obs_date
     AND g.YEARSTOMATURITY = l.YEARSTOMATURITY
     AND g.CURVECODE = 10
    WHERE l.CURVECODE = 199
)
SELECT
    obs_date,
    CASE
        WHEN YEARSTOMATURITY = 3 THEN '3Y'
        WHEN YEARSTOMATURITY = 5 THEN '5Y'
        WHEN YEARSTOMATURITY = 10 THEN '10Y'
    END AS term,
    spread_bp
FROM daily_spread
ORDER BY obs_date, term;
