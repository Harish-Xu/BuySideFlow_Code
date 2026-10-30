WITH quarterly_raw AS (
    SELECT
        DATE(m.ENDDATE) AS quarter_end_date,
        CONCAT(YEAR(m.ENDDATE), 'Q', QUARTER(m.ENDDATE)) AS quarter_label,
        m.DATAVALUE AS gdp_yoy,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    WHERE m.INDICATORCODE = 110252269
      AND DATE(m.ENDDATE) >= DATE('2020-01-01')
      AND DATE(m.ENDDATE) <= DATE('2025-12-31')
      AND m.DATAVALUE IS NOT NULL
),
quarterly_values AS (
    SELECT
        quarter_end_date,
        quarter_label,
        gdp_yoy
    FROM quarterly_raw
    WHERE rn = 1
),
extreme_values AS (
    SELECT
        MAX(gdp_yoy) AS max_gdp_yoy,
        MIN(gdp_yoy) AS min_gdp_yoy
    FROM quarterly_values
)
SELECT
    ROUND(e.max_gdp_yoy, 1) AS `最高GDP同比`,
    GROUP_CONCAT(
        CASE WHEN q.gdp_yoy = e.max_gdp_yoy THEN q.quarter_label END
        ORDER BY q.quarter_end_date
        SEPARATOR ', '
    ) AS `最高值出现季度`,
    ROUND(e.min_gdp_yoy, 1) AS `最低GDP同比`,
    GROUP_CONCAT(
        CASE WHEN q.gdp_yoy = e.min_gdp_yoy THEN q.quarter_label END
        ORDER BY q.quarter_end_date
        SEPARATOR ', '
    ) AS `最低值出现季度`
FROM quarterly_values q
CROSS JOIN extreme_values e
GROUP BY e.max_gdp_yoy, e.min_gdp_yoy;
