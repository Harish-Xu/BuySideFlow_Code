WITH params AS (
    SELECT DATE('2025-10-31') AS observe_date
),
export_raw AS (
    SELECT
        DATE(m.ENDDATE) AS end_date,
        m.DATAVALUE AS export_yoy,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    JOIN params p
      ON DATE(m.ENDDATE) <= p.observe_date
     AND m.INFOPUBLDATE < DATE_ADD(p.observe_date, INTERVAL 1 DAY)
    WHERE m.INDICATORCODE = 110006286
      AND m.DATAVALUE IS NOT NULL
),
export_monthly AS (
    SELECT
        end_date,
        export_yoy
    FROM export_raw
    WHERE rn = 1
),
zscore_calc AS (
    SELECT
        curr.end_date,
        curr.export_yoy,
        AVG(hist.export_yoy) AS baseline_mean,
        STDDEV_POP(hist.export_yoy) AS baseline_std,
        COUNT(*) AS baseline_months
    FROM export_monthly curr
    JOIN export_monthly hist
      ON hist.end_date >= DATE_SUB(curr.end_date, INTERVAL 12 MONTH)
     AND hist.end_date < curr.end_date
    GROUP BY curr.end_date, curr.export_yoy
),
scored AS (
    SELECT
        end_date,
        export_yoy,
        (export_yoy - baseline_mean) / NULLIF(baseline_std, 0) AS z_score
    FROM zscore_calc
    WHERE baseline_months = 12
      AND baseline_std > 0
)
SELECT
    DATE_FORMAT(end_date, '%Y-%m') AS `年月`,
    ROUND(export_yoy, 1) AS `出口同比`,
    ROUND(z_score, 2) AS `Z-score`
FROM scored
WHERE z_score > 2
ORDER BY end_date;
