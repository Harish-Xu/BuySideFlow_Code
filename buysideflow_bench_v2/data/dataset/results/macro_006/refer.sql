WITH params AS (
    SELECT DATE('2025-10-31') AS observe_date
),
ppi_raw AS (
    SELECT
        m.INDICATORCODE,
        DATE(m.ENDDATE) AS end_date,
        DATE_FORMAT(m.ENDDATE, '%Y-%m') AS ym,
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
        110251402, -- PPI:全部工业品:环比:月
        110251401  -- PPI:全部工业品:当期同比:月
    )
),
ppi_monthly AS (
    SELECT
        ym,
        MAX(end_date) AS end_date,
        MAX(CASE WHEN INDICATORCODE = 110251402 THEN DATAVALUE END) AS ppi_mom,
        MAX(CASE WHEN INDICATORCODE = 110251401 THEN DATAVALUE END) AS ppi_yoy
    FROM ppi_raw
    WHERE rn = 1
    GROUP BY ym
    HAVING ppi_mom IS NOT NULL
       AND ppi_yoy IS NOT NULL
),
latest_month AS (
    SELECT *
    FROM ppi_monthly
    ORDER BY ym DESC
    LIMIT 1
),
prev_month AS (
    SELECT pm.*
    FROM ppi_monthly pm
    JOIN latest_month lm
      ON pm.ym = DATE_FORMAT(
          DATE_SUB(STR_TO_DATE(CONCAT(lm.ym, '-01'), '%Y-%m-%d'), INTERVAL 1 MONTH),
          '%Y-%m'
      )
)
SELECT
    lm.end_date AS `截止日期`,
    ROUND(lm.ppi_mom, 1) AS `PPI环比涨跌幅`,
    ROUND(lm.ppi_yoy, 1) AS `PPI同比涨跌幅`,
    ROUND(lm.ppi_mom - pm.ppi_mom, 1) AS `PPI环比较上月变化`,
    ROUND(lm.ppi_yoy - pm.ppi_yoy, 1) AS `PPI同比较上月变化`
FROM latest_month lm
JOIN prev_month pm
  ON 1 = 1;
