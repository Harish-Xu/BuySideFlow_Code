WITH params AS (
    SELECT DATE('2025-10-17') AS as_of_date
),
visible_quarters AS (
    SELECT
        DATE(m.ENDDATE) AS period_end_date,
        m.DATAVALUE AS gdp_yoy,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC, m.ID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    JOIN params p
      ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE m.INDICATORCODE = 110000002
      AND m.DATAVALUE IS NOT NULL
      AND m.ENDDATE <= p.as_of_date
),
latest_period AS (
    SELECT MAX(period_end_date) AS latest_end_date
    FROM visible_quarters
    WHERE rn = 1
),
comparison_periods AS (
    SELECT latest_end_date,
           DATE_SUB(latest_end_date, INTERVAL 1 YEAR) AS prior_same_period,
           DATE(CONCAT(YEAR(latest_end_date) - 1, '-12-31')) AS prior_year_end
    FROM latest_period
),
targets AS (
    SELECT latest_end_date AS period_end_date FROM comparison_periods
    UNION
    SELECT prior_year_end FROM comparison_periods
    UNION
    SELECT prior_same_period FROM comparison_periods
)
SELECT
    v.period_end_date AS `统计期末日期`,
    ROUND(v.gdp_yoy, 2) AS `GDP同比增速`
FROM visible_quarters v
JOIN targets t ON t.period_end_date = v.period_end_date
WHERE v.rn = 1
ORDER BY v.period_end_date DESC;
