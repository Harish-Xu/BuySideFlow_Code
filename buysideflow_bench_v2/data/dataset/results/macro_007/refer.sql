WITH params AS (
    SELECT DATE('2025-10-31') AS observe_date
),
indicator_raw AS (
    SELECT
        m.INDICATORCODE,
        DATE(m.ENDDATE) AS end_date,
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
        110255493, -- CPI:当期同比:月
        110251401  -- PPI:全部工业品:当期同比:月
    )
      AND m.DATAVALUE IS NOT NULL
),
monthly_values AS (
    SELECT
        end_date,
        MAX(CASE WHEN INDICATORCODE = 110255493 THEN DATAVALUE END) AS cpi_yoy,
        MAX(CASE WHEN INDICATORCODE = 110251401 THEN DATAVALUE END) AS ppi_yoy
    FROM indicator_raw
    WHERE rn = 1
    GROUP BY end_date
    HAVING cpi_yoy IS NOT NULL
       AND ppi_yoy IS NOT NULL
),
positive_months AS (
    SELECT
        end_date,
        cpi_yoy - ppi_yoy AS yoy_diff,
        TIMESTAMPDIFF(MONTH, DATE('1970-01-01'), end_date)
          - ROW_NUMBER() OVER (ORDER BY end_date) AS grp
    FROM monthly_values
    WHERE cpi_yoy - ppi_yoy > 0
),
positive_intervals AS (
    SELECT
        MIN(end_date) AS start_date,
        MAX(end_date) AS end_date,
        COUNT(*) AS duration_months
    FROM positive_months
    GROUP BY grp
    HAVING COUNT(*) >= 3
)
SELECT
    start_date AS `开始时间`,
    end_date AS `结束时间`,
    duration_months AS `持续月数`
FROM positive_intervals
ORDER BY start_date;
