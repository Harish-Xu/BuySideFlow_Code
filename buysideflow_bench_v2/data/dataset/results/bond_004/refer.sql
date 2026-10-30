SELECT
    DATE(c.ENDDATE) AS curve_date,
    c.CURVECODE AS curve_code,
    c.YEARSTOMATURITY AS maturity_year,
    c.YIELD AS yield_value
FROM bond_cbyieldcurve c FORCE INDEX (IX_BOND_CBYIELDCURVE)
WHERE c.CURVECODE IN (10, 195)
  AND c.YIELDTYPECODE = 1
  AND c.YEARSTOMATURITY IN (1, 3, 5, 7, 10)
  AND c.YIELD IS NOT NULL
  AND c.ENDDATE >= DATE('{{fetch_start_date}}')
  AND c.ENDDATE < DATE_ADD(DATE('{{as_of_date}}'), INTERVAL 1 DAY)
ORDER BY curve_date, curve_code, maturity_year, c.UPDATETIME, c.ID;
