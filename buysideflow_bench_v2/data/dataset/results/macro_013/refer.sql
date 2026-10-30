WITH params AS (
    SELECT DATE('2025-07-10') AS observe_date
),
macro_raw AS (
    SELECT
        m.INDICATORCODE,
        DATE(m.ENDDATE) AS end_date,
        YEAR(m.ENDDATE) AS year,
        QUARTER(m.ENDDATE) AS quarter,
        DATE_FORMAT(m.ENDDATE, '%Y-%m') AS month_key,
        m.DATAVALUE,
        ROW_NUMBER() OVER (
            PARTITION BY m.INDICATORCODE, DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    JOIN params p
      ON m.ENDDATE < DATE_ADD(p.observe_date, INTERVAL 1 DAY)
     AND m.INFOPUBLDATE < DATE_ADD(p.observe_date, INTERVAL 1 DAY)
    WHERE m.INDICATORCODE IN (110252269, 110255493)
      AND m.ENDDATE >= DATE('2021-01-01')
),
gdp_quarterly AS (
    SELECT
        year,
        quarter,
        DATAVALUE AS gdp_yoy
    FROM macro_raw
    WHERE INDICATORCODE = 110252269 -- GDP(不变价):当期同比:季
      AND rn = 1 AND DATAVALUE IS NOT NULL
),
cpi_monthly AS (
    SELECT
        end_date,
        year,
        quarter,
        month_key,
        DATAVALUE AS cpi_yoy
    FROM macro_raw
    WHERE INDICATORCODE = 110255493 -- CPI:当期同比:月
      AND rn = 1 AND DATAVALUE IS NOT NULL
)
SELECT
    c.month_key AS `月份`,
    ROUND(g.gdp_yoy, 1) AS `GDP同比`,
    ROUND(c.cpi_yoy, 1) AS `CPI同比`
FROM cpi_monthly c
JOIN gdp_quarterly g
  ON g.year = c.year
 AND g.quarter = c.quarter
WHERE g.gdp_yoy > 5
  AND c.cpi_yoy < 2.5
ORDER BY c.end_date;
