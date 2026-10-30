WITH
params AS (
    SELECT
        DATE('2026-01-09') AS start_date,
        DATE('2026-01-16') AS end_date
),
tenors AS (
    SELECT 1 AS tenor_year, 1 AS sort_order UNION ALL
    SELECT 2, 2 UNION ALL
    SELECT 3, 3 UNION ALL
    SELECT 5, 4 UNION ALL
    SELECT 7, 5 UNION ALL
    SELECT 10, 6
),
start_curve AS (
    SELECT
        t.tenor_year,
        t.sort_order,
        c.YIELD AS start_yield
    FROM tenors t
    JOIN bond_cbyieldcurve c
      ON c.YEARSTOMATURITY = t.tenor_year
    JOIN params p
      ON c.ENDDATE = p.start_date
    WHERE c.CURVECODE = 280
      AND c.YIELDTYPECODE = 1
      AND c.YIELD IS NOT NULL
),
end_curve AS (
    SELECT
        t.tenor_year,
        c.YIELD AS end_yield
    FROM tenors t
    JOIN bond_cbyieldcurve c
      ON c.YEARSTOMATURITY = t.tenor_year
    JOIN params p
      ON c.ENDDATE = p.end_date
    WHERE c.CURVECODE = 280
      AND c.YIELDTYPECODE = 1
      AND c.YIELD IS NOT NULL
)
SELECT
    CONCAT(s.tenor_year, 'Y') AS `期限`,
    ROUND(s.start_yield * 100, 4) AS `上周五收益率(%)`,
    ROUND(e.end_yield * 100, 4) AS `本周五收益率(%)`,
    ROUND((e.end_yield - s.start_yield) * 10000, 2) AS `周变动(bp)`
FROM start_curve s
JOIN end_curve e
  ON e.tenor_year = s.tenor_year
ORDER BY s.sort_order;
