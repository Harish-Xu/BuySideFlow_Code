WITH params AS (
    SELECT
        DATE('2026-03-02') AS as_of_date,
        DATE('2025-04-01') AS start_date,
        DATE('2026-03-01') AS asof_month_start
),
yield_spread_daily AS (
    SELECT
        c.ENDDATE,
        MAX(CASE WHEN c.YEARSTOMATURITY = 10 THEN c.YIELD END) AS yield_10y,
        MAX(CASE WHEN c.YEARSTOMATURITY = 1 THEN c.YIELD END) AS yield_1y,
        MAX(CASE WHEN c.YEARSTOMATURITY = 10 THEN c.YIELD END)
        - MAX(CASE WHEN c.YEARSTOMATURITY = 1 THEN c.YIELD END) AS term_spread
    FROM bond_cbyieldcurve c FORCE INDEX (IX_BOND_CBYIELDCURVE)
    JOIN params p
    WHERE c.CURVECODE = 10
      AND c.YIELDTYPECODE = 1
      AND c.YEARSTOMATURITY IN (1, 10)
      AND c.YIELD IS NOT NULL
      AND c.ENDDATE >= p.start_date
      AND c.ENDDATE < p.asof_month_start
    GROUP BY c.ENDDATE
    HAVING yield_10y IS NOT NULL
       AND yield_1y IS NOT NULL
),
monthly_spread_stats AS (
    SELECT
        DATE_SUB(ENDDATE, INTERVAL DAYOFMONTH(ENDDATE) - 1 DAY) AS month_start,
        AVG(term_spread) AS avg_spread
    FROM yield_spread_daily
    GROUP BY DATE_SUB(ENDDATE, INTERVAL DAYOFMONTH(ENDDATE) - 1 DAY)
),
ranked_spread AS (
    SELECT
        month_start,
        avg_spread,
        ROW_NUMBER() OVER (ORDER BY avg_spread, month_start) AS rn,
        COUNT(*) OVER () AS total_cnt
    FROM monthly_spread_stats
),
pmi_data AS (
    SELECT
        DATE_SUB(ENDDATE, INTERVAL DAYOFMONTH(ENDDATE) - 1 DAY) AS month_start,
        pmi_value
    FROM (
        SELECT
            ENDDATE,
            DATAVALUE AS pmi_value,
            ROW_NUMBER() OVER (
                PARTITION BY YEAR(ENDDATE), MONTH(ENDDATE) 
                ORDER BY ENDDATE DESC, INFOPUBLDATE DESC, ID DESC
            ) AS rn
        FROM c_ed_macroindicatordata
        JOIN params p
        WHERE INDICATORCODE = 110166523
          AND ENDDATE >= p.start_date
          AND ENDDATE < p.asof_month_start
          AND INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
          AND DATAVALUE IS NOT NULL
    ) ranked_pmi
    WHERE rn = 1
),
combined_data AS (
    SELECT
        s.month_start,
        s.avg_spread,
        p.pmi_value,
        CASE
            WHEN s.rn <= CEIL(s.total_cnt * 0.10) OR s.avg_spread < 0 THEN 1
            ELSE 0
        END AS is_extreme_flat,
        CASE WHEN p.pmi_value < 50 THEN 1 ELSE 0 END AS is_pmi_below_50
    FROM ranked_spread s
    LEFT JOIN pmi_data p
      ON s.month_start = p.month_start
    WHERE p.pmi_value IS NOT NULL
)
SELECT
    '极度平坦或倒挂组' AS `分组`,
    COUNT(*) AS `纳入PMI统计的月份数`,
    SUM(is_pmi_below_50) AS `PMI低于50的月份数`,
    ROUND(SUM(is_pmi_below_50) / NULLIF(COUNT(*), 0), 4) AS `PMI低于50的月份占比`
FROM combined_data
WHERE is_extreme_flat = 1
UNION ALL
SELECT
    '对照组' AS `分组`,
    COUNT(*) AS `纳入PMI统计的月份数`,
    SUM(is_pmi_below_50) AS `PMI低于50的月份数`,
    ROUND(SUM(is_pmi_below_50) / NULLIF(COUNT(*), 0), 4) AS `PMI低于50的月份占比`
FROM combined_data
WHERE is_extreme_flat = 0;
