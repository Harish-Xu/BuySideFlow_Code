WITH common_days AS (
    SELECT
        c.ENDDATE,
        c.YIELD AS gov10_yield,
        r.FR007 AS fr007
    FROM bond_cbyieldcurve c
    JOIN bond_baseratereference r
      ON c.ENDDATE = r.ENDDATE
    WHERE c.CURVECODE = 10
      AND c.YIELDTYPECODE = 1
      AND c.YEARSTOMATURITY = 10
      AND c.YIELD IS NOT NULL
      AND r.FR007 IS NOT NULL
      AND c.ENDDATE >= DATE('2024-11-01')
      AND c.ENDDATE < DATE('2025-01-01')
),
changes AS (
    SELECT
        DATE(ENDDATE) AS signal_date,
        ROUND(
            (gov10_yield - LAG(gov10_yield) OVER (ORDER BY ENDDATE)) * 10000,
            2
        ) AS gov10_change_bp,
        ROUND(
            (fr007 - LAG(fr007) OVER (ORDER BY ENDDATE)) * 100,
            2
        ) AS fr007_change_bp
    FROM common_days
),
signals AS (
    SELECT
        signal_date,
        gov10_change_bp,
        fr007_change_bp,
        ROUND(fr007_change_bp - gov10_change_bp, 2) AS divergence_bp
    FROM changes
    WHERE signal_date >= DATE('2024-12-01')
      AND signal_date < DATE('2025-01-01')
      AND gov10_change_bp < 0
      AND fr007_change_bp > 0
),
ranked AS (
    SELECT
        signal_date,
        gov10_change_bp,
        fr007_change_bp,
        divergence_bp,
        COUNT(*) OVER () AS trigger_days,
        FIRST_VALUE(signal_date) OVER (
            ORDER BY divergence_bp DESC, signal_date ASC
        ) AS max_divergence_date,
        MAX(divergence_bp) OVER () AS max_divergence_bp
    FROM signals
)
SELECT
    signal_date AS `日期`,
    gov10_change_bp AS `10Y变动(bp)`,
    fr007_change_bp AS `FR007变动(bp)`,
    divergence_bp AS `背离幅度(bp)`,
    trigger_days AS `触发天数`,
    max_divergence_date AS `最大背离日`,
    max_divergence_bp AS `最大背离幅度(bp)`
FROM ranked
ORDER BY divergence_bp DESC, signal_date ASC;
