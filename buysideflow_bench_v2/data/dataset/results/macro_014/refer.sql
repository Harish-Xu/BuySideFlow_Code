WITH params AS (
    SELECT DATE('2025-07-25') AS observe_date
),
macro_raw AS (
    SELECT
        m.INDICATORCODE,
        DATE(m.ENDDATE) AS end_date,
        YEAR(m.ENDDATE) AS year,
        QUARTER(m.ENDDATE) AS quarter,
        m.DATAVALUE,
        ROW_NUMBER() OVER (
            PARTITION BY m.INDICATORCODE, DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    JOIN params p
      ON m.ENDDATE < DATE_ADD(p.observe_date, INTERVAL 1 DAY)
     AND m.INFOPUBLDATE < DATE_ADD(p.observe_date, INTERVAL 1 DAY)
    WHERE m.INDICATORCODE IN (
        110252269, -- GDP(不变价):当期同比:季
        110255493  -- CPI:当期同比:月
    )
      AND m.DATAVALUE IS NOT NULL
      AND m.INFOPUBLDATE IS NOT NULL
),
latest_gdp AS (
    SELECT
        end_date AS quarter_end_date,
        year,
        quarter,
        DATAVALUE AS gdp_yoy
    FROM macro_raw
    WHERE INDICATORCODE = 110252269
      AND rn = 1
    ORDER BY end_date DESC
    LIMIT 1
),
quarter_end_cpi AS (
    SELECT
        end_date,
        DATAVALUE AS cpi_yoy
    FROM macro_raw
    WHERE INDICATORCODE = 110255493
      AND rn = 1
)
SELECT
    CONCAT(g.year, 'Q', g.quarter) AS `统计期`,
    ROUND(g.gdp_yoy, 1) AS `GDP同比`,
    ROUND(c.cpi_yoy, 1) AS `CPI同比`,
    CASE
        WHEN g.gdp_yoy >= 5 THEN '扩张'
        WHEN g.gdp_yoy < 3 AND c.cpi_yoy <= 3 THEN '衰退'
        WHEN g.gdp_yoy >= 3 AND g.gdp_yoy < 5 AND c.cpi_yoy <= 3 THEN '复苏'
        WHEN g.gdp_yoy < 5 AND c.cpi_yoy > 3 THEN '滞胀'
        ELSE '其他'
    END AS `经济状态`
FROM latest_gdp g
JOIN quarter_end_cpi c
  ON c.end_date = g.quarter_end_date;
