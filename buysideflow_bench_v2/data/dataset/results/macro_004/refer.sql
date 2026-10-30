WITH params AS (
    SELECT DATE('2026-01-20') AS as_of_date
),
latest_month AS (
    SELECT m.ENDDATE, m.DATAVALUE
    FROM c_ed_macroindicatordata m
    CROSS JOIN params p
    -- Published monthly export YoY (%), USD, source: GACC.
    WHERE m.INDICATORCODE = 110006286
      AND m.ENDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    ORDER BY m.ENDDATE DESC, m.INFOPUBLDATE DESC, m.JSID DESC
    LIMIT 1
),
annual_yoy AS (
    SELECT m.ENDDATE, m.DATAVALUE, m.INFOPUBLDATE, m.JSID
    FROM c_ed_macroindicatordata m
    CROSS JOIN params p
    -- December cumulative export YoY is the published annual rate.
    WHERE m.INDICATORCODE = 110006276
      AND m.ENDDATE IN ('2024-12-31', '2025-12-31')
      AND m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
)
SELECT
    (SELECT DATE_FORMAT(ENDDATE, '%Y-%m') FROM latest_month) AS `月份`,
    (SELECT ROUND(DATAVALUE, 1) FROM latest_month) AS `该月出口同比`,
    (
        SELECT ROUND(DATAVALUE, 1)
        FROM annual_yoy
        WHERE ENDDATE = '2025-12-31'
        ORDER BY INFOPUBLDATE DESC, JSID DESC
        LIMIT 1
    ) AS `2025年全年出口同比`,
    (
        SELECT ROUND(DATAVALUE, 1)
        FROM annual_yoy
        WHERE ENDDATE = '2024-12-31'
        ORDER BY INFOPUBLDATE DESC, JSID DESC
        LIMIT 1
    ) AS `2024年全年出口同比`;
