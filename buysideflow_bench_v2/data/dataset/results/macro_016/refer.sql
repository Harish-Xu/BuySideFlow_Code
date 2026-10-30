WITH indicator_raw AS (
    SELECT
        m.INDICATORCODE,
        DATE(m.ENDDATE) AS quarter_end_date,
        YEAR(m.ENDDATE) AS year,
        QUARTER(m.ENDDATE) AS quarter,
        YEAR(m.ENDDATE) * 4 + QUARTER(m.ENDDATE) AS quarter_index,
        m.DATAVALUE,
        ROW_NUMBER() OVER (
            PARTITION BY m.INDICATORCODE, DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    WHERE m.INDICATORCODE IN (
        110252269, -- GDP(不变价):当期同比:季
        110240588  -- GDP(不变价):环比:季调:季
    )
      AND DATE(m.ENDDATE) >= DATE('2020-01-01')
      AND DATE(m.ENDDATE) <= DATE('2025-12-31')
      AND m.DATAVALUE IS NOT NULL
),
quarterly_values AS (
    SELECT
        quarter_end_date,
        year,
        quarter,
        quarter_index,
        CONCAT(year, 'Q', quarter) AS quarter_label,
        MAX(CASE WHEN INDICATORCODE = 110252269 THEN DATAVALUE END) AS gdp_yoy,
        MAX(CASE WHEN INDICATORCODE = 110240588 THEN DATAVALUE END) AS gdp_qoq
    FROM indicator_raw
    WHERE rn = 1
    GROUP BY quarter_end_date, year, quarter, quarter_index
    HAVING gdp_qoq IS NOT NULL
),
weak_quarters AS (
    SELECT
        q.*,
        q.quarter_index - ROW_NUMBER() OVER (ORDER BY q.quarter_end_date) AS grp
    FROM quarterly_values q
    WHERE q.gdp_qoq < 1.0
),
weak_groups AS (
    SELECT
        grp,
        MIN(quarter_end_date) AS start_date,
        MAX(quarter_end_date) AS end_date,
        COUNT(*) AS duration_quarters
    FROM weak_quarters
    GROUP BY grp
    HAVING COUNT(*) >= 2
)
SELECT
    CONCAT(YEAR(g.start_date), 'Q', QUARTER(g.start_date)) AS `区间起始季度`,
    CONCAT(YEAR(g.end_date), 'Q', QUARTER(g.end_date)) AS `区间结束季度`,
    q.quarter_label AS `季度`,
    ROUND(q.gdp_yoy, 1) AS `GDP同比增速`,
    ROUND(q.gdp_qoq, 1) AS `GDP季调环比增速`
FROM weak_quarters q
JOIN weak_groups g
  ON q.grp = g.grp
ORDER BY q.quarter_end_date;
