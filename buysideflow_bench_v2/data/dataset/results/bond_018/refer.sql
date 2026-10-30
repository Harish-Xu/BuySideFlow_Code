WITH curve_ranked AS (
    SELECT
        DATE(ENDDATE) AS obs_date,
        YEARSTOMATURITY,
        YIELD,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(ENDDATE), YEARSTOMATURITY
            ORDER BY ID DESC
        ) AS rn
    FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE CURVECODE = 10
      AND YIELDTYPECODE = 1
      AND YEARSTOMATURITY IN (1,5,10,30)
      AND ENDDATE >= '2024-12-01'
      AND ENDDATE < '2025-01-01'
      AND YIELD IS NOT NULL
),
daily_curve AS (
    SELECT
        obs_date,
        MAX(CASE WHEN YEARSTOMATURITY = 1 THEN YIELD END) AS y1,
        MAX(CASE WHEN YEARSTOMATURITY = 5 THEN YIELD END) AS y5,
        MAX(CASE WHEN YEARSTOMATURITY = 10 THEN YIELD END) AS y10,
        MAX(CASE WHEN YEARSTOMATURITY = 30 THEN YIELD END) AS y30
    FROM curve_ranked
    WHERE rn = 1
    GROUP BY obs_date
    HAVING y1 IS NOT NULL
       AND y5 IS NOT NULL
       AND y10 IS NOT NULL
       AND y30 IS NOT NULL
),
last_two AS (
    SELECT
        d.*,
        ROW_NUMBER() OVER (ORDER BY obs_date DESC) AS date_rank
    FROM daily_curve d
),
paired AS (
    SELECT
        MAX(CASE WHEN date_rank = 1 THEN obs_date END) AS current_curve_date,
        MAX(CASE WHEN date_rank = 2 THEN obs_date END) AS previous_curve_date,
        MAX(CASE WHEN date_rank = 1 THEN y1 END) AS c1,
        MAX(CASE WHEN date_rank = 1 THEN y5 END) AS c5,
        MAX(CASE WHEN date_rank = 1 THEN y10 END) AS c10,
        MAX(CASE WHEN date_rank = 1 THEN y30 END) AS c30,
        MAX(CASE WHEN date_rank = 2 THEN y1 END) AS p1,
        MAX(CASE WHEN date_rank = 2 THEN y5 END) AS p5,
        MAX(CASE WHEN date_rank = 2 THEN y10 END) AS p10,
        MAX(CASE WHEN date_rank = 2 THEN y30 END) AS p30
    FROM last_two
    WHERE date_rank <= 2
    HAVING COUNT(*) = 2
),
changes AS (
    SELECT
        current_curve_date,
        previous_curve_date,
        (c10 - p10) * 10000 AS d10,
        ((c10 - c1) - (p10 - p1)) * 10000 AS d10_1,
        ((c30 - c10) - (p30 - p10)) * 10000 AS d30_10,
        ((c10 - c5) - (p10 - p5)) * 10000 AS d10_5
    FROM paired
),
stats AS (
    SELECT
        *,
        d10_1 + d30_10 + d10_5
            - GREATEST(d10_1, d30_10, d10_5)
            - LEAST(d10_1, d30_10, d10_5) AS slope_median
    FROM changes
)
SELECT
    current_curve_date AS `当前曲线日`,
    previous_curve_date AS `前一曲线日`,
    ROUND(d10, 2) AS `10Y收益率变化`,
    ROUND(d10_1, 2) AS `10Y-1Y利差变化`,
    ROUND(d30_10, 2) AS `30Y-10Y利差变化`,
    ROUND(d10_5, 2) AS `10Y-5Y利差变化`,
    ROUND(slope_median, 2) AS `利差变化中位数`,
    CASE
        WHEN d10 < 0 AND slope_median > 0 THEN '牛陡'
        WHEN d10 < 0 THEN '牛平'
        WHEN d10 > 0 AND slope_median > 0 THEN '熊陡'
        WHEN d10 > 0 THEN '熊平'
        WHEN slope_median > 0 THEN '中性陡'
        ELSE '中性平'
    END AS `曲线形态`
FROM stats;
