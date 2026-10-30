WITH cn_ranked AS (
    SELECT
        DATE(ENDDATE) AS obs_date,
        YIELD * 100 AS metric_value,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(ENDDATE), YEARSTOMATURITY
            ORDER BY UPDATETIME DESC, ID DESC
        ) AS rn
    FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE CURVECODE = 10
      AND YIELDTYPECODE = 1
      AND YEARSTOMATURITY = 10
      AND YIELD IS NOT NULL
      AND ENDDATE >= '2023-01-01'
      AND ENDDATE < '2025-01-01'
),
us_ranked AS (
    SELECT
        DATE(ENDDATE) AS obs_date,
        YIELDRATE AS metric_value,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(ENDDATE), REMAINMATURITY
            ORDER BY UPDATETIME DESC, ID DESC
        ) AS rn
    FROM bond_sapyieldcurve
    WHERE CURVECODE = 188
      AND YIELDTYPECODE = 1
      AND REMAINMATURITY = 10
      AND YIELDRATE IS NOT NULL
      AND ENDDATE >= '2023-01-01'
      AND ENDDATE < '2025-01-01'
),
fx_ranked AS (
    SELECT
        DATE(ENDDATE) AS obs_date,
        PERIODENDPRICE / 100 AS metric_value,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(ENDDATE), CURRENCY, DATAREPORTPERIOD, QUOTATIONTYPE
            ORDER BY XGRQ DESC, ID DESC
        ) AS rn
    FROM ed_rmbbaseexchangerate
    WHERE DATAREPORTPERIOD = 5
      AND CURRENCY = 1000
      AND QUOTATIONTYPE = 1
      AND PERIODENDPRICE IS NOT NULL
      AND ENDDATE >= '2023-01-01'
      AND ENDDATE < '2025-01-01'
)
SELECT 'cn10_pct' AS series_name, obs_date, metric_value
FROM cn_ranked
WHERE rn = 1
UNION ALL
SELECT 'us10_pct' AS series_name, obs_date, metric_value
FROM us_ranked
WHERE rn = 1
UNION ALL
SELECT 'usdcny_mid' AS series_name, obs_date, metric_value
FROM fx_ranked
WHERE rn = 1
ORDER BY obs_date, series_name;
