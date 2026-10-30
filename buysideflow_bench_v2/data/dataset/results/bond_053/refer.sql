-- refer.py uses this curve extraction, then computes rolling 1Y p90/median,
-- 2025 high-spread signal starts and 10-common-curve-day event windows in
-- Python.

WITH ranked AS (
    SELECT
        DATE(c.ENDDATE) AS obs_date,
        c.CURVECODE,
        c.YIELD,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(c.ENDDATE), c.CURVECODE
            ORDER BY c.UPDATETIME DESC, c.ID DESC
        ) AS rn
    FROM bond_cbyieldcurve c FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE c.ENDDATE >= '2023-12-01'
      AND c.ENDDATE < '2026-01-06'
      AND c.CURVECODE IN (66, 69)
      AND c.YIELDTYPECODE = 1
      AND c.YEARSTOMATURITY = 5
      AND c.YIELD IS NOT NULL
)
SELECT
    obs_date,
    MAX(CASE WHEN CURVECODE = 69 THEN YIELD END) AS yield_aaa,
    MAX(CASE WHEN CURVECODE = 66 THEN YIELD END) AS yield_aa
FROM ranked
WHERE rn = 1
GROUP BY obs_date
HAVING yield_aaa IS NOT NULL
   AND yield_aa IS NOT NULL
ORDER BY obs_date;
