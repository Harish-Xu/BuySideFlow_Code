WITH ranked_curve AS (
    SELECT
        DATE(c.ENDDATE) AS curve_date,
        c.CURVECODE AS curve_code,
        c.YEARSTOMATURITY AS maturity_year,
        c.YIELD AS yield_value,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(c.ENDDATE), c.CURVECODE, c.YEARSTOMATURITY
            ORDER BY c.ID DESC
        ) AS rn
    FROM bond_cbyieldcurve c FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE c.CURVECODE IN (68, 69)
      AND c.YIELDTYPECODE = 1
      AND c.YEARSTOMATURITY = 5
      AND c.ENDDATE >= DATE_SUB(DATE('{{as_of_date}}'), INTERVAL 13 MONTH)
      AND c.ENDDATE < DATE_ADD(DATE('{{as_of_date}}'), INTERVAL 1 DAY)
      AND c.YIELD IS NOT NULL
)
SELECT
    curve_date,
    curve_code,
    yield_value
FROM ranked_curve
WHERE rn = 1
ORDER BY curve_date, curve_code;
