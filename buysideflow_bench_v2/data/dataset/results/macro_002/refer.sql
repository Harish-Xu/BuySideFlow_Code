WITH annual_raw AS (
    SELECT
        m.INDICATORCODE,
        YEAR(m.ENDDATE) AS year,
        m.DATAVALUE,
        ROW_NUMBER() OVER (
            PARTITION BY m.INDICATORCODE, YEAR(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    WHERE m.INDICATORCODE IN (120013414, 120013421, 120013420, 120013419)
      AND YEAR(m.ENDDATE) BETWEEN 2020 AND 2024
),
annual_values AS (
    SELECT
        year,
        MAX(CASE WHEN INDICATORCODE = 120013414 THEN DATAVALUE END) AS gdp_current,
        MAX(CASE WHEN INDICATORCODE = 120013421 THEN DATAVALUE END) AS first_industry_current,
        MAX(CASE WHEN INDICATORCODE = 120013420 THEN DATAVALUE END) AS second_industry_current,
        MAX(CASE WHEN INDICATORCODE = 120013419 THEN DATAVALUE END) AS third_industry_current
    FROM annual_raw
    WHERE rn = 1
    GROUP BY year
)
SELECT
    year,
    ROUND(first_industry_current / NULLIF(gdp_current, 0) * 100, 2) AS `第一产业占比`,
    ROUND(second_industry_current / NULLIF(gdp_current, 0) * 100, 2) AS `第二产业占比`,
    ROUND(third_industry_current / NULLIF(gdp_current, 0) * 100, 2) AS `第三产业占比`
FROM annual_values
ORDER BY year;
