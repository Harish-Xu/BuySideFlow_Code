WITH curve_ranked AS (
    SELECT
        DATE(ENDDATE) AS ENDDATE,
        YEARSTOMATURITY,
        YIELD,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(ENDDATE), YEARSTOMATURITY
            ORDER BY ID DESC
        ) AS rn
    FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE ENDDATE >= '2024-01-01'
      AND ENDDATE < '2026-01-01'
      AND CURVECODE = 10
      AND YIELDTYPECODE = 1
      AND YEARSTOMATURITY IN (10, 30)
      AND YIELD IS NOT NULL
),
daily_curve AS (
    SELECT
        ENDDATE,
        MAX(CASE WHEN YEARSTOMATURITY = 10 THEN YIELD END) AS y10,
        MAX(CASE WHEN YEARSTOMATURITY = 30 THEN YIELD END) AS y30
    FROM curve_ranked
    WHERE rn = 1
    GROUP BY ENDDATE
    HAVING y10 IS NOT NULL
       AND y30 IS NOT NULL
),
spread AS (
    SELECT
        ENDDATE,
        y10,
        y30,
        y30 - y10 AS spread_30_10
    FROM daily_curve
),
extrema AS (
    SELECT
        MAX(spread_30_10) AS max_spread,
        MIN(spread_30_10) AS min_spread
    FROM spread
),
steepest AS (
    SELECT
        1 AS sort_key,
        'steepest' AS extreme_type,
        s.ENDDATE,
        ROUND(s.spread_30_10 * 10000, 2) AS spread_bp,
        ROUND(s.y10 * 100, 4) AS y10_pct,
        ROUND(s.y30 * 100, 4) AS y30_pct
    FROM spread s
    JOIN extrema e
      ON s.spread_30_10 = e.max_spread
    ORDER BY s.ENDDATE ASC
    LIMIT 1
),
flattest AS (
    SELECT
        2 AS sort_key,
        'flattest' AS extreme_type,
        s.ENDDATE,
        ROUND(s.spread_30_10 * 10000, 2) AS spread_bp,
        ROUND(s.y10 * 100, 4) AS y10_pct,
        ROUND(s.y30 * 100, 4) AS y30_pct
    FROM spread s
    JOIN extrema e
      ON s.spread_30_10 = e.min_spread
    ORDER BY s.ENDDATE ASC
    LIMIT 1
)
SELECT
    extreme_type,
    ENDDATE,
    spread_bp,
    y10_pct,
    y30_pct
FROM (
    SELECT * FROM steepest
    UNION ALL
    SELECT * FROM flattest
) x
ORDER BY sort_key;
