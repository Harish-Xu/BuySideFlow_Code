WITH swap_ranked AS (
    SELECT
        DATE(ENDDATE) AS obs_date,
        DATAVALUE AS swap_5y_rate_pct,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(ENDDATE)
            ORDER BY INFOPUBLDATE DESC, UPDATETIME DESC, ID DESC
        ) AS rn
    FROM c_ed_macroindicatordata
    WHERE INDICATORCODE = 600960007
      AND ENDDATE >= DATE('2024-01-01')
      AND ENDDATE < DATE('2026-02-01')
      AND DATAVALUE IS NOT NULL
      AND INFOPUBLDATE < DATE_ADD(DATE(ENDDATE), INTERVAL 1 DAY)
),
gov_ranked AS (
    SELECT
        DATE(ENDDATE) AS obs_date,
        YIELD AS gov_5y_yield_decimal,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(ENDDATE), CURVECODE, YIELDTYPECODE, YEARSTOMATURITY
            ORDER BY UPDATETIME DESC, ID DESC
        ) AS rn
    FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE ENDDATE >= DATE('2024-01-01')
      AND ENDDATE < DATE('2026-02-01')
      AND CURVECODE = 10
      AND YIELDTYPECODE = 1
      AND YEARSTOMATURITY = 5
      AND YIELD IS NOT NULL
)
SELECT
    s.obs_date,
    s.swap_5y_rate_pct,
    g.gov_5y_yield_decimal,
    g.gov_5y_yield_decimal * 100 AS gov_5y_yield_pct,
    (s.swap_5y_rate_pct - g.gov_5y_yield_decimal * 100) * 100 AS spread_bp
FROM swap_ranked s
JOIN gov_ranked g
  ON g.obs_date = s.obs_date
WHERE s.rn = 1
  AND g.rn = 1
ORDER BY s.obs_date;
