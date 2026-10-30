WITH params AS (
    SELECT DATE('2025-09-30') AS observe_date
),
gdp_raw AS (
    SELECT
        DATE(m.ENDDATE) AS quarter_end_date,
        YEAR(m.ENDDATE) AS year,
        QUARTER(m.ENDDATE) AS quarter,
        m.DATAVALUE AS gdp_yoy,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    JOIN params p
      ON DATE(m.ENDDATE) <= p.observe_date
     AND m.INFOPUBLDATE < DATE_ADD(p.observe_date, INTERVAL 1 DAY)
    WHERE m.INDICATORCODE = 110252269  -- GDP(不变价):当期同比:季, i.e. real growth
      AND m.INFOPUBLDATE IS NOT NULL
),
gdp_quarterly AS (
    SELECT
        quarter_end_date,
        year,
        quarter,
        gdp_yoy
    FROM gdp_raw
    WHERE rn = 1 AND gdp_yoy IS NOT NULL
),
unemployment_raw AS (
    SELECT
        DATE(m.ENDDATE) AS month_end_date,
        YEAR(m.ENDDATE) AS year,
        QUARTER(m.ENDDATE) AS quarter,
        m.DATAVALUE AS unemployment_rate,
        ROW_NUMBER() OVER (
            PARTITION BY YEAR(m.ENDDATE), MONTH(m.ENDDATE)
            ORDER BY COALESCE(m.INFOPUBLDATE, e.available_by) DESC, m.JSID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    JOIN params p
      ON DATE(m.ENDDATE) <= p.observe_date
    -- The January 2020 row lacks INFOPUBLDATE; NBS published 5.3 on
    -- 2020-03-16 15:00 (same value-specific evidence as macro_003).
    -- Any other NULL publication date stays unavailable.
    LEFT JOIN (
        SELECT '2020-01-31' AS end_date, 5.3 AS published_value,
               '2020-03-16 15:00:00' AS available_by
    ) e
      ON m.INFOPUBLDATE IS NULL
     AND DATE(m.ENDDATE) = e.end_date
     AND m.DATAVALUE = e.published_value
    WHERE m.INDICATORCODE = 110208050
      AND COALESCE(m.INFOPUBLDATE, e.available_by) < DATE_ADD(p.observe_date, INTERVAL 1 DAY)
),
unemployment_quarterly AS (
    SELECT
        year,
        quarter,
        AVG(unemployment_rate) AS avg_unemployment
    FROM unemployment_raw
    WHERE rn = 1
    GROUP BY year, quarter
    HAVING COUNT(DISTINCT MONTH(month_end_date)) = 3
       AND COUNT(unemployment_rate) = 3
),
quarterly_data AS (
    SELECT
        g.quarter_end_date,
        g.year * 4 + g.quarter AS quarter_index,
        g.year,
        g.quarter,
        g.gdp_yoy,
        u.avg_unemployment
    FROM gdp_quarterly g
    JOIN unemployment_quarterly u
      ON u.year = g.year
     AND u.quarter = g.quarter
),
rolling_stats AS (
    SELECT
        quarter_end_date,
        MIN(quarter_end_date) OVER w AS start_quarter_end_date,
        quarter_index,
        MIN(quarter_index) OVER w AS start_quarter_index,
        COUNT(*) OVER w AS n,
        SUM(gdp_yoy) OVER w AS sum_gdp,
        SUM(avg_unemployment) OVER w AS sum_unemployment,
        SUM(gdp_yoy * avg_unemployment) OVER w AS sum_xy,
        SUM(gdp_yoy * gdp_yoy) OVER w AS sum_x2,
        SUM(avg_unemployment * avg_unemployment) OVER w AS sum_y2
    FROM quarterly_data
    WINDOW w AS (
        ORDER BY quarter_end_date
        ROWS BETWEEN 11 PRECEDING AND CURRENT ROW
    )
),
correlation_base AS (
    SELECT
        start_quarter_end_date,
        quarter_end_date,
        n,
        (n * sum_xy - sum_gdp * sum_unemployment) AS numerator,
        (n * sum_x2 - POW(sum_gdp, 2)) AS x_denom,
        (n * sum_y2 - POW(sum_unemployment, 2)) AS y_denom
    FROM rolling_stats
    WHERE n = 12
      AND quarter_index - start_quarter_index = 11
),
window_corr AS (
    SELECT
        start_quarter_end_date,
        quarter_end_date,
        GREATEST(-1, LEAST(1, numerator / NULLIF(SQRT(x_denom * y_denom), 0))) AS correlation_coefficient
    FROM correlation_base
    WHERE x_denom > 0
      AND y_denom > 0
),
window_test AS (
    SELECT
        start_quarter_end_date,
        quarter_end_date,
        correlation_coefficient,
        correlation_coefficient * SQRT(10) / NULLIF(SQRT(1 - POW(correlation_coefficient, 2)), 0) AS t_statistic
    FROM window_corr
)
SELECT
    CONCAT(YEAR(start_quarter_end_date), 'Q', QUARTER(start_quarter_end_date)) AS `开始季度`,
    CONCAT(YEAR(quarter_end_date), 'Q', QUARTER(quarter_end_date)) AS `结束季度`,
    ROUND(correlation_coefficient, 4) AS `相关系数`
FROM window_test
WHERE correlation_coefficient < 0
  AND (correlation_coefficient <= -1 OR ABS(t_statistic) > 2.2281388519649385)
ORDER BY start_quarter_end_date;
