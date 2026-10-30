WITH params AS (
    SELECT DATE('2025-10-20') AS observe_date
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
        176598898, -- CPI(上年同月=100):扣除食品和能源:月
        110255493  -- CPI:当期同比:月
    )
      AND m.DATAVALUE IS NOT NULL
),
monthly_values AS (
    SELECT
        end_date,
        MAX(CASE WHEN INDICATORCODE = 176598898 THEN DATAVALUE - 100 END) AS core_cpi_yoy,
        MAX(CASE WHEN INDICATORCODE = 110255493 THEN DATAVALUE END) AS cpi_yoy
    FROM indicator_raw
    WHERE rn = 1
    GROUP BY end_date
    HAVING core_cpi_yoy IS NOT NULL
       AND cpi_yoy IS NOT NULL
),
rolling_stats AS (
    SELECT
        end_date,
        COUNT(*) OVER w AS window_size,
        SUM(core_cpi_yoy) OVER w AS sum_core,
        SUM(cpi_yoy) OVER w AS sum_cpi,
        SUM(core_cpi_yoy * cpi_yoy) OVER w AS sum_core_cpi,
        SUM(core_cpi_yoy * core_cpi_yoy) OVER w AS sum_core_sq,
        SUM(cpi_yoy * cpi_yoy) OVER w AS sum_cpi_sq
    FROM monthly_values
    WINDOW w AS (
        ORDER BY end_date
        ROWS BETWEEN 23 PRECEDING AND CURRENT ROW
    )
),
rolling_corr AS (
    SELECT
        end_date,
        CASE
            WHEN window_size = 24
             AND (window_size * sum_core_sq - POW(sum_core, 2)) > 0
             AND (window_size * sum_cpi_sq - POW(sum_cpi, 2)) > 0
            THEN
                (window_size * sum_core_cpi - sum_core * sum_cpi)
                / SQRT(
                    (window_size * sum_core_sq - POW(sum_core, 2))
                    * (window_size * sum_cpi_sq - POW(sum_cpi, 2))
                )
        END AS rolling_correlation
    FROM rolling_stats
)
SELECT
    end_date AS `截止日期`,
    ROUND(rolling_correlation, 4) AS `滚动相关系数`
FROM rolling_corr
WHERE end_date BETWEEN DATE('2025-01-01') AND (SELECT observe_date FROM params)
  AND rolling_correlation IS NOT NULL
ORDER BY end_date;
