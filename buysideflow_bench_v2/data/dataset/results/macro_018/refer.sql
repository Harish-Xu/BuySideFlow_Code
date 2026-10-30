WITH indicator_raw AS (
    SELECT
        m.INDICATORCODE,
        DATE(m.ENDDATE) AS month_end_date,
        DATE_FORMAT(m.ENDDATE, '%Y-%m') AS month_label,
        m.DATAVALUE,
        ROW_NUMBER() OVER (
            PARTITION BY m.INDICATORCODE, DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    WHERE m.INDICATORCODE IN (
        110251401, -- PPI:当期同比:月
        110255493  -- CPI:当期同比:月
    )
      AND DATE(m.ENDDATE) BETWEEN DATE('2021-11-01') AND DATE('2024-10-31')
      AND m.INFOPUBLDATE < DATE('2024-11-10')
      AND m.DATAVALUE IS NOT NULL
),
monthly_values_raw AS (
    SELECT
        month_end_date,
        month_label,
        MAX(CASE WHEN INDICATORCODE = 110251401 THEN DATAVALUE END) AS ppi_yoy,
        MAX(CASE WHEN INDICATORCODE = 110255493 THEN DATAVALUE END) AS cpi_yoy
    FROM indicator_raw
    WHERE rn = 1
    GROUP BY month_end_date, month_label
    HAVING ppi_yoy IS NOT NULL
       AND cpi_yoy IS NOT NULL
),
monthly_values AS (
    SELECT *
    FROM monthly_values_raw
    WHERE (SELECT COUNT(*) FROM monthly_values_raw) = 36
),
spread_values AS (
    SELECT
        month_end_date,
        month_label,
        ppi_yoy,
        cpi_yoy,
        ppi_yoy - cpi_yoy AS spread
    FROM monthly_values
),
ranked AS (
    SELECT
        s.*,
        RANK() OVER (ORDER BY spread DESC) AS rank_desc,
        CUME_DIST() OVER (ORDER BY spread) AS percentile_low_to_high
    FROM spread_values s
),
target_rows AS (
    SELECT '剪刀差最大' AS item, r.*
    FROM ranked r
    WHERE r.spread = (SELECT MAX(spread) FROM ranked)

    UNION ALL

    SELECT '剪刀差最小' AS item, r.*
    FROM ranked r
    WHERE r.spread = (SELECT MIN(spread) FROM ranked)

    UNION ALL

    SELECT '2024-10' AS item, r.*
    FROM ranked r
    WHERE r.month_label = '2024-10'
)
SELECT
    item AS `项目`,
    month_label AS `年月`,
    ppi_yoy AS `PPI同比`,
    cpi_yoy AS `CPI同比`,
    spread AS `剪刀差`,
    rank_desc AS `剪刀差排名`,
    percentile_low_to_high AS `剪刀差历史分位`
FROM target_rows
ORDER BY
    CASE item
        WHEN '剪刀差最大' THEN 1
        WHEN '剪刀差最小' THEN 2
        ELSE 3
    END,
    month_end_date;
