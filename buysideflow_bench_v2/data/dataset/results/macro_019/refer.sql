WITH params AS (
    SELECT DATE('2025-10-31') AS as_of_date
),
indicator_raw AS (
    SELECT
        m.INDICATORCODE,
        DATE(m.ENDDATE) AS month_end_date,
        DATE_FORMAT(m.ENDDATE, '%Y-%m') AS month_label,
        YEAR(m.ENDDATE) * 12 + MONTH(m.ENDDATE) AS month_index,
        m.DATAVALUE,
        ROW_NUMBER() OVER (
            PARTITION BY m.INDICATORCODE, DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    JOIN params p
      ON m.ENDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
     AND m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE m.INDICATORCODE IN (
        110255493, -- CPI:当期同比:月
        110111411, -- 货币和准货币(M2):同比:月
        150001512  -- 社会融资规模增量:当期同比:月
    )
      AND m.ENDDATE >= DATE('2020-01-01')
      AND m.INFOPUBLDATE IS NOT NULL
      AND m.DATAVALUE IS NOT NULL
),
monthly_values AS (
    SELECT
        month_end_date,
        month_label,
        month_index,
        MAX(CASE WHEN INDICATORCODE = 110255493 THEN DATAVALUE END) AS cpi_yoy,
        MAX(CASE WHEN INDICATORCODE = 110111411 THEN DATAVALUE END) AS m2_yoy,
        MAX(CASE WHEN INDICATORCODE = 150001512 THEN DATAVALUE END) AS social_financing_yoy
    FROM indicator_raw
    WHERE rn = 1
    GROUP BY month_end_date, month_label, month_index
),
ranked_cpi_months AS (
    SELECT
        month_end_date,
        month_label,
        month_index,
        cpi_yoy,
        m2_yoy,
        social_financing_yoy,
        ROW_NUMBER() OVER (ORDER BY month_end_date DESC) AS recent_rank
    FROM monthly_values
    WHERE cpi_yoy IS NOT NULL
),
recent_window AS (
    SELECT *
    FROM ranked_cpi_months
    WHERE recent_rank <= 6
),
recent_negative_months AS (
    SELECT
        month_end_date,
        month_index,
        month_index - ROW_NUMBER() OVER (ORDER BY month_end_date) AS grp
    FROM recent_window
    WHERE cpi_yoy < 0
),
recent_negative_groups AS (
    SELECT
        grp,
        COUNT(*) AS duration_months
    FROM recent_negative_months
    GROUP BY grp
),
recent_stats AS (
    SELECT
        CAST(SUM(CASE WHEN cpi_yoy < 0 THEN 1 ELSE 0 END) AS UNSIGNED) AS recent_negative_months,
        COALESCE((SELECT MAX(duration_months) FROM recent_negative_groups), 0) AS recent_max_consecutive_months,
        ROUND(
            CASE WHEN COUNT(*) = 6 AND COUNT(m2_yoy) = 6
                 THEN AVG(m2_yoy) END,
            1
        ) AS recent_m2_yoy_avg,
        ROUND(
            CASE WHEN COUNT(*) = 6 AND COUNT(social_financing_yoy) = 6
                 THEN AVG(social_financing_yoy) END,
            1
        ) AS recent_social_financing_yoy_avg
    FROM recent_window
),
cpi_history AS (
    SELECT *
    FROM monthly_values
    WHERE cpi_yoy IS NOT NULL
),
historical_negative_months AS (
    SELECT
        month_end_date,
        month_index,
        month_index - ROW_NUMBER() OVER (ORDER BY month_end_date) AS grp
    FROM cpi_history
    WHERE cpi_yoy < 0
),
historical_negative_groups AS (
    SELECT
        grp,
        MIN(month_end_date) AS start_date,
        MAX(month_end_date) AS end_date,
        COUNT(*) AS duration_months
    FROM historical_negative_months
    GROUP BY grp
),
longest_historical_groups AS (
    SELECT
        *
    FROM historical_negative_groups
    WHERE duration_months = (SELECT MAX(duration_months) FROM historical_negative_groups)
),
historical_indicator_stats AS (
    SELECT
        g.start_date,
        g.end_date,
        g.duration_months,
        ROUND(
            CASE WHEN COUNT(m.m2_yoy) = g.duration_months
                 THEN AVG(m.m2_yoy) END,
            1
        ) AS historical_m2_yoy_avg,
        ROUND(
            CASE WHEN COUNT(m.social_financing_yoy) = g.duration_months
                 THEN AVG(m.social_financing_yoy) END,
            1
        ) AS historical_social_financing_yoy_avg
    FROM longest_historical_groups g
    JOIN monthly_values m
      ON m.month_end_date BETWEEN g.start_date AND g.end_date
    GROUP BY g.start_date, g.end_date, g.duration_months
)
SELECT
    rs.recent_negative_months AS `近6期负增长月数`,
    rs.recent_max_consecutive_months AS `近6期最长连续月数`,
    CONCAT(DATE_FORMAT(h.start_date, '%Y-%m'), '至', DATE_FORMAT(h.end_date, '%Y-%m')) AS `历史最长区间`,
    h.duration_months AS `历史持续月数`,
    rs.recent_m2_yoy_avg AS `近6期M2同比均值`,
    rs.recent_social_financing_yoy_avg AS `近6期社融增量同比均值`,
    h.historical_m2_yoy_avg AS `历史区间M2同比均值`,
    h.historical_social_financing_yoy_avg AS `历史区间社融增量同比均值`
FROM recent_stats rs
CROSS JOIN historical_indicator_stats h
ORDER BY h.start_date;
