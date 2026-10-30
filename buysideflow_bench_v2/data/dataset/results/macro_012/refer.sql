WITH params AS (
    SELECT DATE('2025-09-19') AS observe_date
),
ppi_raw AS (
    SELECT
        DATE(m.ENDDATE) AS end_date,
        m.DATAVALUE AS ppi_yoy,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    JOIN params p
      ON DATE(m.ENDDATE) <= p.observe_date
     AND m.INFOPUBLDATE < DATE_ADD(p.observe_date, INTERVAL 1 DAY)
    WHERE m.INDICATORCODE = 110251401
      AND m.DATAVALUE IS NOT NULL
),
ppi_monthly AS (
    SELECT
        end_date,
        ppi_yoy
    FROM ppi_raw
    WHERE rn = 1
),
zscore_calc AS (
    SELECT
        curr.end_date,
        curr.ppi_yoy,
        AVG(hist.ppi_yoy) AS baseline_mean,
        STDDEV_POP(hist.ppi_yoy) AS baseline_std,
        COUNT(*) AS baseline_months
    FROM ppi_monthly curr
    JOIN ppi_monthly hist
      ON hist.end_date >= DATE_SUB(curr.end_date, INTERVAL 36 MONTH)
     AND hist.end_date < curr.end_date
    GROUP BY curr.end_date, curr.ppi_yoy
),
scored AS (
    SELECT
        end_date,
        ppi_yoy,
        (ppi_yoy - baseline_mean) / NULLIF(baseline_std, 0) AS z_score
    FROM zscore_calc
    WHERE baseline_months = 36
      AND baseline_std > 0
)
SELECT
    DATE_FORMAT(end_date, '%Y-%m') AS `月份`,
    ROUND(ppi_yoy, 1) AS `PPI同比`,
    ROUND(z_score, 2) AS `Z-score`
FROM scored
WHERE ABS(z_score) > 1.5
ORDER BY end_date;
