WITH params AS (
    SELECT DATE('2025-11-24') AS observe_date
),
lag_values AS (
    SELECT 0 AS lead_months UNION ALL
    SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL
    SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6
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
      ON DATE(m.ENDDATE) <= p.observe_date
     AND m.INFOPUBLDATE < DATE_ADD(p.observe_date, INTERVAL 1 DAY)
    WHERE m.INDICATORCODE IN (
        110251401, -- PPI:当期同比:月
        110255493  -- CPI:当期同比:月
    )
      AND m.DATAVALUE IS NOT NULL
),
monthly_values AS (
    SELECT
        month_end_date,
        month_label,
        month_index,
        MAX(CASE WHEN INDICATORCODE = 110251401 THEN DATAVALUE END) AS ppi_yoy,
        MAX(CASE WHEN INDICATORCODE = 110255493 THEN DATAVALUE END) AS cpi_yoy
    FROM indicator_raw
    WHERE rn = 1
    GROUP BY month_end_date, month_label, month_index
    HAVING ppi_yoy IS NOT NULL
       AND cpi_yoy IS NOT NULL
),
latest_month AS (
    SELECT
        MAX(month_index) AS latest_month_index
    FROM monthly_values
),
cpi_sample AS (
    SELECT
        m.*
    FROM monthly_values m
    JOIN latest_month l
      ON m.month_index BETWEEN l.latest_month_index - 59 AND l.latest_month_index
),
lagged_pairs AS (
    SELECT
        lv.lead_months,
        c.month_label AS sample_month,
        c.cpi_yoy,
        p.ppi_yoy
    FROM lag_values lv
    JOIN cpi_sample c
    JOIN monthly_values p
      ON p.month_index = c.month_index - lv.lead_months
),
lag_correlations AS (
    SELECT
        lead_months,
        COUNT(*) AS sample_count,
        MIN(sample_month) AS sample_start_month,
        MAX(sample_month) AS sample_end_month,
        (
            COUNT(*) * SUM(ppi_yoy * cpi_yoy)
            - SUM(ppi_yoy) * SUM(cpi_yoy)
        ) / NULLIF(
            SQRT(
                GREATEST(0, COUNT(*) * SUM(ppi_yoy * ppi_yoy) - POW(SUM(ppi_yoy), 2))
                * GREATEST(0, COUNT(*) * SUM(cpi_yoy * cpi_yoy) - POW(SUM(cpi_yoy), 2))
            ),
            0
        ) AS correlation
    FROM lagged_pairs
    GROUP BY lead_months
    HAVING sample_count = 60
),
best_positive_lag AS (
    SELECT
        lead_months,
        correlation,
        sample_start_month,
        sample_end_month
    FROM lag_correlations
    WHERE correlation > 0
    ORDER BY correlation DESC, lead_months
    LIMIT 1
)
SELECT
    lead_months AS `领先月数`,
    ROUND(correlation, 2) AS `相关系数`,
    sample_start_month AS `样本起始月`,
    sample_end_month AS `样本结束月`
FROM best_positive_lag;
