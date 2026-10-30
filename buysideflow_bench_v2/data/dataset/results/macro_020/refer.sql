WITH indicator_raw AS (
    SELECT
        m.INDICATORCODE,
        DATE(m.ENDDATE) AS end_date,
        YEAR(m.ENDDATE) AS year,
        QUARTER(m.ENDDATE) AS quarter,
        MONTH(m.ENDDATE) AS month,
        m.DATAVALUE,
        ROW_NUMBER() OVER (
            PARTITION BY m.INDICATORCODE, DATE(m.ENDDATE)
            ORDER BY
                CASE WHEN m.INFOPUBLDATE IS NULL THEN 1 ELSE 0 END,
                m.INFOPUBLDATE DESC,
                m.JSID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    WHERE m.INDICATORCODE IN (
        110111411, -- 货币和准货币(M2):同比:月
        110255821, -- GDP:初步核算数:当期值:季
        150006354  -- 社会融资规模存量:同比:月
    )
      AND DATE(m.ENDDATE) BETWEEN DATE('2021-10-01') AND DATE('2025-09-30')
      AND m.DATAVALUE IS NOT NULL
),
quarter_end_monthly AS (
    SELECT
        year,
        quarter,
        CONCAT(year, 'Q', quarter) AS quarter_label,
        MAX(CASE WHEN INDICATORCODE = 110111411 THEN DATAVALUE END) AS m2_yoy,
        MAX(CASE WHEN INDICATORCODE = 150006354 THEN DATAVALUE END) AS social_financing_yoy
    FROM indicator_raw
    WHERE rn = 1
      AND INDICATORCODE IN (110111411, 150006354)
      AND month IN (3, 6, 9, 12)
    GROUP BY year, quarter
),
gdp_current_value AS (
    SELECT
        year,
        quarter,
        DATAVALUE AS nominal_gdp_value
    FROM indicator_raw
    WHERE rn = 1
      AND INDICATORCODE = 110255821
),
gdp_yoy AS (
    SELECT
        cur.year,
        cur.quarter,
        (cur.nominal_gdp_value / prev.nominal_gdp_value - 1) * 100 AS nominal_gdp_yoy
    FROM gdp_current_value cur
    JOIN gdp_current_value prev
      ON cur.year = prev.year + 1
     AND cur.quarter = prev.quarter
    WHERE prev.nominal_gdp_value > 0
),
sample_quarters AS (
    SELECT
        m.year,
        m.quarter,
        m.quarter_label,
        m.m2_yoy,
        g.nominal_gdp_yoy,
        m.social_financing_yoy,
        m.m2_yoy - g.nominal_gdp_yoy AS m2_minus_gdp
    FROM quarter_end_monthly m
    JOIN gdp_yoy g
      ON g.year = m.year
     AND g.quarter = m.quarter
    WHERE (
          (m.year = 2022 AND m.quarter = 4)
       OR m.year IN (2023, 2024)
       OR (m.year = 2025 AND m.quarter <= 3)
    )
      AND m.m2_yoy IS NOT NULL
      AND g.nominal_gdp_yoy IS NOT NULL
      AND m.social_financing_yoy IS NOT NULL
),
spread_stats AS (
    SELECT
        COUNT(*) AS sample_quarters,
        AVG(m2_minus_gdp) AS avg_m2_minus_gdp
    FROM sample_quarters
),
correlation_stats AS (
    SELECT
        (
            COUNT(*) * SUM(m2_minus_gdp * social_financing_yoy)
            - SUM(m2_minus_gdp) * SUM(social_financing_yoy)
        ) / NULLIF(
            SQRT(
                GREATEST(0, COUNT(*) * SUM(m2_minus_gdp * m2_minus_gdp) - POW(SUM(m2_minus_gdp), 2))
                * GREATEST(0, COUNT(*) * SUM(social_financing_yoy * social_financing_yoy) - POW(SUM(social_financing_yoy), 2))
            ),
            0
        ) AS correlation
    FROM sample_quarters
),
q3_2025 AS (
    SELECT
        m2_minus_gdp AS q3_2025_m2_minus_gdp
    FROM sample_quarters
    WHERE year = 2025
      AND quarter = 3
)
SELECT
    ROUND(q.q3_2025_m2_minus_gdp, 1) AS `2025Q3差值`,
    ROUND(s.avg_m2_minus_gdp, 1) AS `12季度平均差值`,
    ROUND(q.q3_2025_m2_minus_gdp - s.avg_m2_minus_gdp, 1) AS `2025Q3较12季度均值差`,
    ROUND(c.correlation, 3) AS `差值与社融增速相关系数`
FROM q3_2025 q
CROSS JOIN spread_stats s
CROSS JOIN correlation_stats c
WHERE s.sample_quarters = 12;
