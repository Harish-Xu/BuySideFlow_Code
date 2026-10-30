WITH target_month AS (
    SELECT DATE('2025-09-30') AS end_date
),
cpi_raw AS (
    SELECT
        m.INDICATORCODE,
        DATE(m.ENDDATE) AS end_date,
        m.DATAVALUE,
        ROW_NUMBER() OVER (
            PARTITION BY m.INDICATORCODE, DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    JOIN target_month t
      ON DATE(m.ENDDATE) = t.end_date
    WHERE m.INDICATORCODE IN (
        176626030, -- CPI:环比:月
        110255493  -- CPI:当期同比:月
    )
),
cpi_values AS (
    SELECT
        MAX(CASE WHEN INDICATORCODE = 176626030 THEN DATAVALUE END) AS cpi_mom,
        MAX(CASE WHEN INDICATORCODE = 110255493 THEN DATAVALUE END) AS cpi_yoy
    FROM cpi_raw
    WHERE rn = 1
),
core_raw AS (
    SELECT
        DATE(m.ENDDATE) AS end_date,
        m.DATAVALUE AS core_cpi_index,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    JOIN target_month t
      ON DATE(m.ENDDATE) <= t.end_date
    WHERE m.INDICATORCODE = 176598898
),
core_values AS (
    SELECT
        end_date,
        core_cpi_index,
        core_cpi_index - 100 AS core_cpi_yoy
    FROM core_raw
    WHERE rn = 1
),
core_direction AS (
    SELECT
        end_date,
        core_cpi_yoy,
        CASE
            WHEN core_cpi_index > LAG(core_cpi_index) OVER (ORDER BY end_date) THEN '扩大'
            WHEN core_cpi_index < LAG(core_cpi_index) OVER (ORDER BY end_date) THEN '收窄'
            WHEN LAG(core_cpi_index) OVER (ORDER BY end_date) IS NOT NULL THEN '持平'
        END AS direction
    FROM core_values
),
core_current AS (
    SELECT
        cd.end_date,
        cd.core_cpi_yoy,
        cd.direction
    FROM core_direction cd
    JOIN target_month t
      ON cd.end_date = t.end_date
),
last_direction_break AS (
    SELECT
        MAX(cd.end_date) AS break_date
    FROM core_direction cd
    CROSS JOIN core_current cur
    WHERE cd.end_date < cur.end_date
      AND cd.direction <> cur.direction
),
direction_count AS (
    SELECT
        COUNT(*) AS continue_months
    FROM core_direction cd
    CROSS JOIN core_current cur
    LEFT JOIN last_direction_break lb
      ON 1 = 1
    WHERE cd.end_date <= cur.end_date
      AND cd.direction = cur.direction
      AND cd.direction IS NOT NULL
      AND (lb.break_date IS NULL OR cd.end_date > lb.break_date)
)
SELECT
    DATE_FORMAT(t.end_date, '%Y-%m-%d') AS `截止日期`,
    CONCAT(ROUND(c.cpi_mom, 1), '%') AS `CPI环比涨跌幅`,
    CONCAT(ROUND(c.cpi_yoy, 1), '%') AS `CPI同比涨跌幅`,
    CONCAT(ROUND(cur.core_cpi_yoy, 1), '%') AS `核心CPI同比涨跌幅`,
    cur.direction AS `核心CPI同比变化方向`,
    dc.continue_months AS `核心CPI当前方向持续月数`
FROM target_month t
CROSS JOIN cpi_values c
CROSS JOIN core_current cur
CROSS JOIN direction_count dc;
