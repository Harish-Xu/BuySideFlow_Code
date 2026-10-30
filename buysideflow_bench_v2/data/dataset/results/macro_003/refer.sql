-- Observation date: 2025-10-12, including releases during that calendar day.
WITH period_def AS (
    SELECT '2025年以来' AS period_label, DATE('2025-01-01') AS start_date,
           DATE('2025-09-01') AS end_exclusive, 8 AS expected_months, 1 AS sort_order
    UNION ALL SELECT '2025年上半年', '2025-01-01', '2025-07-01', 6, 2
    UNION ALL SELECT '2021年', '2021-01-01', '2022-01-01', 12, 3
    UNION ALL SELECT '2022年', '2022-01-01', '2023-01-01', 12, 4
    UNION ALL SELECT '2023年', '2023-01-01', '2024-01-01', 12, 5
    UNION ALL SELECT '2024年', '2024-01-01', '2025-01-01', 12, 6
), available_ranked AS (
    SELECT
        DATE(ENDDATE) AS end_date,
        DATAVALUE,
        ROW_NUMBER() OVER (
            PARTITION BY DATE_FORMAT(ENDDATE, '%Y-%m')
            ORDER BY INFOPUBLDATE DESC, JSID DESC, ID DESC
        ) AS rn
    FROM c_ed_macroindicatordata
    WHERE INDICATORCODE = 110208050
      AND ENDDATE >= '2021-01-01'
      AND ENDDATE < '2025-09-01'
      AND INFOPUBLDATE < '2025-10-13'
), monthly_values AS (
    SELECT end_date, DATAVALUE
    FROM available_ranked
    WHERE rn = 1
)
SELECT
    p.period_label AS `统计期`,
    CASE
        WHEN COUNT(m.end_date) = p.expected_months
         AND COUNT(CASE WHEN m.DATAVALUE BETWEEN 0 AND 100 THEN 1 END) = p.expected_months
        THEN ROUND(AVG(m.DATAVALUE), 4)
    END AS `失业率平均值`
FROM period_def p
LEFT JOIN monthly_values m
  ON m.end_date >= p.start_date
 AND m.end_date < p.end_exclusive
GROUP BY p.period_label, p.sort_order, p.expected_months
ORDER BY p.sort_order;
