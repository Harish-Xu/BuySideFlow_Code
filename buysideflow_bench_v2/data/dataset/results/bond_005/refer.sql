WITH ranked_curve AS (
    SELECT
        DATE(c.ENDDATE) AS curve_date,
        c.YEARSTOMATURITY AS maturity_year,
        c.YIELD AS yield_value,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(c.ENDDATE), c.YEARSTOMATURITY
            ORDER BY c.ID DESC
        ) AS rn
    FROM bond_cbyieldcurve c FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE c.CURVECODE = 10
      AND c.YIELDTYPECODE = 1
      AND c.YEARSTOMATURITY IN (2, 5, 10)
      AND c.ENDDATE >= DATE('{{history_start}}')
      AND c.ENDDATE < DATE_ADD(DATE('{{as_of_date}}'), INTERVAL 1 DAY)
      AND c.YIELD IS NOT NULL
)
SELECT
    curve_date,
    maturity_year,
    yield_value
FROM ranked_curve
WHERE rn = 1
ORDER BY curve_date, maturity_year;
