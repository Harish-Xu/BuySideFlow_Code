WITH prior_curve_day AS (
    SELECT
        MAX(ENDDATE) AS prior_enddate
    FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE CURVECODE = 10
      AND YIELDTYPECODE = 1
      AND YEARSTOMATURITY = 10
      AND YIELD IS NOT NULL
      AND ENDDATE < '2024-01-01'
),
curve_ranked AS (
    SELECT
        DATE(ENDDATE) AS ENDDATE,
        YIELD,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(ENDDATE)
            ORDER BY ID DESC
        ) AS rn
    FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE CURVECODE = 10
      AND YIELDTYPECODE = 1
      AND YEARSTOMATURITY = 10
      AND YIELD IS NOT NULL
      AND ENDDATE >= '2024-01-01'
      AND ENDDATE < '2025-01-01'
),
prior_curve_ranked AS (
    SELECT
        DATE(c.ENDDATE) AS ENDDATE,
        c.YIELD,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(c.ENDDATE)
            ORDER BY c.ID DESC
        ) AS rn
    FROM bond_cbyieldcurve c FORCE INDEX (IX_BOND_CBYIELDCURVE)
    JOIN prior_curve_day p
      ON c.ENDDATE = p.prior_enddate
    WHERE c.CURVECODE = 10
      AND c.YIELDTYPECODE = 1
      AND c.YEARSTOMATURITY = 10
      AND c.YIELD IS NOT NULL
),
gov10_raw AS (
    SELECT
        ENDDATE,
        YIELD
    FROM curve_ranked
    WHERE rn = 1
    UNION ALL
    SELECT
        ENDDATE,
        YIELD
    FROM prior_curve_ranked
    WHERE rn = 1
),
gov10 AS (
    SELECT
        ENDDATE,
        YIELD,
        LAG(YIELD) OVER (ORDER BY ENDDATE) AS prev_yield
    FROM gov10_raw
),
weekly_signal AS (
    SELECT
        DATE_SUB(DATE(b.ENDDATE), INTERVAL WEEKDAY(b.ENDDATE) DAY) AS week_start_date,
        COUNT(*) AS signal_days
    FROM bond_baseratereference b FORCE INDEX (IX_BOND_BASERATEREFERENCE)
    JOIN gov10 g
      ON DATE(b.ENDDATE) = g.ENDDATE
    WHERE b.ENDDATE >= '2024-01-01'
      AND b.ENDDATE < '2025-01-01'
      AND b.FR001 IS NOT NULL
      AND b.FR007 IS NOT NULL
      AND b.FR001 > b.FR007
      AND g.prev_yield IS NOT NULL
      AND g.YIELD < g.prev_yield
    GROUP BY DATE_SUB(DATE(b.ENDDATE), INTERVAL WEEKDAY(b.ENDDATE) DAY)
)
SELECT
    week_start_date,
    signal_days
FROM weekly_signal
ORDER BY signal_days DESC, week_start_date ASC
LIMIT 10;
