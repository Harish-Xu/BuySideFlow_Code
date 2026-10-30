-- 财政部-中国地方政府债券收益率曲线: CURVECODE=199;
-- 中债国债收益率曲线: CURVECODE=10. Both use YIELDTYPECODE=1.
WITH ranked_curve AS (
    SELECT
        DATE(ENDDATE) AS obs_date,
        CURVECODE,
        YEARSTOMATURITY,
        YIELD,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(ENDDATE), CURVECODE, YEARSTOMATURITY
            ORDER BY ID DESC
        ) AS rn
    FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE ENDDATE >= DATE('2023-12-01')
      AND ENDDATE < DATE('2025-01-01')
      AND CURVECODE IN (10, 199)
      AND YIELDTYPECODE = 1
      AND YEARSTOMATURITY = 5
      AND YIELD IS NOT NULL
      AND (UPDATETIME IS NULL OR UPDATETIME < DATE_ADD(DATE(ENDDATE), INTERVAL 1 DAY))
),
curve_points AS (
    SELECT
        obs_date,
        CURVECODE,
        YIELD
    FROM ranked_curve
    WHERE rn = 1
),
common_days AS (
    SELECT
        local5.obs_date,
        local5.YIELD AS local5_yield,
        gov5.YIELD AS gov5_yield,
        (local5.YIELD - gov5.YIELD) * 10000 AS spread_bp
    FROM curve_points local5
    JOIN curve_points gov5
      ON local5.obs_date = gov5.obs_date
    WHERE local5.CURVECODE = 199
      AND gov5.CURVECODE = 10
),
changes AS (
    SELECT
        obs_date AS signal_date,
        local5_yield,
        gov5_yield,
        spread_bp,
        spread_bp - LAG(spread_bp) OVER (ORDER BY obs_date) AS daily_change_bp
    FROM common_days
),
signals AS (
    SELECT
        signal_date,
        ROUND(local5_yield * 100, 4) AS local5_yield_pct,
        ROUND(gov5_yield * 100, 4) AS gov5_yield_pct,
        ROUND(spread_bp, 2) AS spread_bp,
        ROUND(daily_change_bp, 2) AS daily_change_bp
    FROM changes
    WHERE signal_date >= DATE('2024-01-01')
      AND signal_date < DATE('2025-01-01')
      AND daily_change_bp > 5
),
ranked AS (
    SELECT
        signal_date,
        local5_yield_pct,
        gov5_yield_pct,
        spread_bp,
        daily_change_bp,
        COUNT(*) OVER () AS trigger_count,
        FIRST_VALUE(signal_date) OVER (
            ORDER BY daily_change_bp DESC, signal_date ASC
        ) AS max_widen_date,
        MAX(daily_change_bp) OVER () AS max_widen_bp
    FROM signals
)
SELECT
    signal_date AS `日期`,
    local5_yield_pct AS `5Y地方债收益率`,
    gov5_yield_pct AS `5Y国债收益率`,
    spread_bp AS `当日利差`,
    daily_change_bp AS `单日变化`,
    trigger_count AS `全年触发次数`,
    max_widen_date AS `最大单日走阔日期`,
    max_widen_bp AS `最大走阔幅度`
FROM ranked
ORDER BY signal_date ASC;
