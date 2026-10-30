WITH params AS (
    SELECT
        DATE('2025-07-10') AS as_of_date,
        DATE('2023-01-01') AS event_start_date
),
cpi_ranked AS (
    SELECT
        CAST(DATE_FORMAT(m.ENDDATE, '%Y-%m-01') AS DATE) AS month_end_date,
        m.DATAVALUE AS cpi_yoy,
        ROW_NUMBER() OVER (
            PARTITION BY DATE_FORMAT(m.ENDDATE, '%Y-%m')
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC, m.ID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    JOIN params p
      ON m.ENDDATE >= DATE_SUB(p.event_start_date, INTERVAL 6 MONTH)
     AND m.ENDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
     AND m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE m.INDICATORCODE = 110255493
),
cpi_monthly AS (
    SELECT month_end_date, cpi_yoy
    FROM cpi_ranked
    WHERE rn = 1 AND cpi_yoy IS NOT NULL
),
negative_months AS (
    SELECT c.month_end_date, c.cpi_yoy
    FROM cpi_monthly c
    JOIN params p
      ON c.month_end_date >= p.event_start_date
    WHERE c.cpi_yoy < 0
),
window_means AS (
    SELECT
        n.month_end_date,
        n.cpi_yoy,
        (
            SELECT AVG(b.cpi_yoy)
            FROM cpi_monthly b
            WHERE b.month_end_date >= DATE_SUB(n.month_end_date, INTERVAL 6 MONTH)
              AND b.month_end_date < n.month_end_date
        ) AS avg_6m_before,
        (
            SELECT AVG(a.cpi_yoy)
            FROM cpi_monthly a
            WHERE a.month_end_date > n.month_end_date
              AND a.month_end_date <= DATE_ADD(n.month_end_date, INTERVAL 6 MONTH)
        ) AS avg_6m_after
    FROM negative_months n
)
SELECT
    DATE_FORMAT(month_end_date, '%Y-%m') AS `年月`,
    ROUND(cpi_yoy, 2) AS `CPI同比`,
    ROUND(avg_6m_before, 2) AS `前6个月均值`,
    ROUND(avg_6m_after, 2) AS `后6个月均值`,
    ROUND(avg_6m_after - avg_6m_before, 2) AS `前后差值`
FROM window_means
ORDER BY month_end_date;
