WITH funding_ranked AS (
    SELECT
        DATE(r.ENDDATE) AS obs_date,
        r.FR007 AS fr007_pct,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(r.ENDDATE)
            ORDER BY r.INSERTTIME DESC, r.ID DESC
        ) AS rn
    FROM bond_baseratereference r FORCE INDEX (IX_BOND_BASERATEREFERENCE)
    WHERE r.ENDDATE >= DATE('2024-01-01')
      AND r.ENDDATE < DATE('2026-02-01')
      AND r.FR007 IS NOT NULL
      AND (r.INSERTTIME IS NULL OR r.INSERTTIME < DATE_ADD(DATE(r.ENDDATE), INTERVAL 1 DAY))
),
curve_ranked AS (
    SELECT
        DATE(c.ENDDATE) AS obs_date,
        c.YEARSTOMATURITY,
        c.YIELD,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(c.ENDDATE), c.YEARSTOMATURITY
            ORDER BY c.UPDATETIME DESC, c.ID DESC
        ) AS rn
    FROM bond_cbyieldcurve c FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE c.ENDDATE >= DATE('2024-01-01')
      AND c.ENDDATE < DATE('2026-02-01')
      AND c.CURVECODE = 10
      AND c.YIELDTYPECODE = 1
      AND c.YEARSTOMATURITY IN (1, 3, 10)
      AND c.YIELD IS NOT NULL
      AND c.UPDATETIME < DATE_ADD(DATE(c.ENDDATE), INTERVAL 1 DAY)
),
curve_pivot AS (
    SELECT
        obs_date,
        MAX(CASE WHEN YEARSTOMATURITY = 1 THEN YIELD END) AS gov_1y_yield,
        MAX(CASE WHEN YEARSTOMATURITY = 3 THEN YIELD END) AS gov_3y_yield,
        MAX(CASE WHEN YEARSTOMATURITY = 10 THEN YIELD END) AS gov_10y_yield
    FROM curve_ranked
    WHERE rn = 1
    GROUP BY obs_date
)
SELECT
    f.obs_date,
    f.fr007_pct,
    c.gov_1y_yield,
    c.gov_3y_yield,
    c.gov_10y_yield
FROM funding_ranked f
JOIN curve_pivot c
  ON c.obs_date = f.obs_date
WHERE f.rn = 1
  AND c.gov_1y_yield IS NOT NULL
  AND c.gov_3y_yield IS NOT NULL
  AND c.gov_10y_yield IS NOT NULL
ORDER BY f.obs_date;
