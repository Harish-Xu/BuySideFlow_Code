WITH funding_ranked AS (
    SELECT
        DATE(ENDDATE) AS obs_date,
        GC007 AS gc007_pct,
        FR007 AS fr007_pct,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(ENDDATE)
            ORDER BY UPDATETIME DESC, ID DESC
        ) AS rn
    FROM bond_baseratereference
    WHERE ENDDATE >= DATE('2025-01-01')
      AND ENDDATE < DATE('2026-01-01')
      AND INSERTTIME < DATE('2026-01-10')
),
funding AS (
    SELECT obs_date, gc007_pct, fr007_pct
    FROM funding_ranked
    WHERE rn = 1 AND gc007_pct IS NOT NULL AND fr007_pct IS NOT NULL
),
curve_ranked AS (
    SELECT
        DATE(ENDDATE) AS obs_date,
        CURVECODE AS curve_code,
        CURVETYPE AS curve_name,
        YEARSTOMATURITY AS tenor_year,
        YIELD AS yield_decimal,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(ENDDATE), CURVECODE, YEARSTOMATURITY
            ORDER BY UPDATETIME DESC, ID DESC
        ) AS rn
    FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE ENDDATE >= DATE('2025-01-01')
      AND ENDDATE < DATE('2026-01-10')
      AND CURVECODE IN (10, 195)
      AND YIELDTYPECODE = 1
      AND YEARSTOMATURITY IN (1, 10)
)
SELECT
    'funding' AS row_type,
    obs_date,
    gc007_pct,
    fr007_pct,
    CAST(NULL AS UNSIGNED) AS curve_code,
    CAST(NULL AS CHAR(100)) AS curve_name,
    CAST(NULL AS DECIMAL(18, 4)) AS tenor_year,
    CAST(NULL AS DECIMAL(18, 8)) AS yield_decimal
FROM funding
UNION ALL
SELECT
    'curve' AS row_type,
    obs_date,
    CAST(NULL AS DECIMAL(18, 12)) AS gc007_pct,
    CAST(NULL AS DECIMAL(18, 12)) AS fr007_pct,
    curve_code,
    curve_name,
    tenor_year,
    yield_decimal
FROM curve_ranked
WHERE rn = 1 AND yield_decimal IS NOT NULL
ORDER BY row_type, obs_date, curve_code, tenor_year;
