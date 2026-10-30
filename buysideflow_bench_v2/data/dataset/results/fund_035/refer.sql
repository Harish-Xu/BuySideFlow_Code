WITH params AS (
    SELECT
        DATE('2025-12-07') AS ref_date,
        DATE_SUB(DATE('2025-12-07'), INTERVAL WEEKDAY(DATE('2025-12-07')) DAY) AS week_start,
        DATE('2025-12-07') AS week_end,
        DATE_SUB(DATE_SUB(DATE('2025-12-07'), INTERVAL WEEKDAY(DATE('2025-12-07')) DAY), INTERVAL 7 DAY) AS prev_week_start,
        DATE_SUB(DATE_SUB(DATE('2025-12-07'), INTERVAL WEEKDAY(DATE('2025-12-07')) DAY), INTERVAL 1 DAY) AS prev_week_end
),
main_fund_universe AS (
    SELECT DISTINCT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        sm.SECUABBR AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain sm
        ON sm.INNERCODE = fa.INNERCODE
       AND sm.SECUCATEGORY = 8
    JOIN params p ON 1 = 1
    WHERE sm.SECUABBR LIKE '%消费%'
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= p.week_end
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.week_end)
      AND (fa.INSERTTIME IS NULL OR fa.INSERTTIME < DATE_ADD(p.week_end, INTERVAL 1 DAY))
      AND fa.XGRQ < DATE_ADD(p.week_end, INTERVAL 1 DAY)
),
nav_candidates AS (
    SELECT
        fu.INNERCODE,
        'target' AS period_label,
        fn.TRADINGDAY,
        fn.UNITNVRESTORED,
        fn.INSERTTIME,
        fn.ID,
        ROW_NUMBER() OVER (
            PARTITION BY fu.INNERCODE, 'target'
            ORDER BY fn.TRADINGDAY DESC, fn.INSERTTIME DESC, fn.ID DESC
        ) AS rn
    FROM main_fund_universe fu
    JOIN mf_fundnetvalueretrans fn
        ON fn.INNERCODE = fu.INNERCODE
    JOIN params p ON 1 = 1
    WHERE fn.TRADINGDAY BETWEEN p.week_start AND p.week_end
      AND fn.UNITNVRESTORED IS NOT NULL
      AND fn.INSERTTIME < DATE_ADD(p.week_end, INTERVAL 1 DAY)
      AND fn.UPDATETIME < DATE_ADD(p.week_end, INTERVAL 1 DAY)

    UNION ALL

    SELECT
        fu.INNERCODE,
        'previous' AS period_label,
        fn.TRADINGDAY,
        fn.UNITNVRESTORED,
        fn.INSERTTIME,
        fn.ID,
        ROW_NUMBER() OVER (
            PARTITION BY fu.INNERCODE, 'previous'
            ORDER BY fn.TRADINGDAY DESC, fn.INSERTTIME DESC, fn.ID DESC
        ) AS rn
    FROM main_fund_universe fu
    JOIN mf_fundnetvalueretrans fn
        ON fn.INNERCODE = fu.INNERCODE
    JOIN params p ON 1 = 1
    WHERE fn.TRADINGDAY BETWEEN p.prev_week_start AND p.prev_week_end
      AND fn.UNITNVRESTORED IS NOT NULL
      AND fn.INSERTTIME < DATE_ADD(p.week_end, INTERVAL 1 DAY)
      AND fn.UPDATETIME < DATE_ADD(p.week_end, INTERVAL 1 DAY)
),
week_nav AS (
    SELECT
        INNERCODE,
        MAX(CASE WHEN period_label = 'target' AND rn = 1 THEN TRADINGDAY END) AS nav_date,
        MAX(CASE WHEN period_label = 'target' AND rn = 1 THEN UNITNVRESTORED END) AS target_nav,
        MAX(CASE WHEN period_label = 'previous' AND rn = 1 THEN UNITNVRESTORED END) AS previous_nav
    FROM nav_candidates
    WHERE rn = 1
    GROUP BY INNERCODE
),
weekly_return AS (
    SELECT
        fu.fund_code,
        fu.fund_name,
        CONCAT(DATE_FORMAT(p.week_start, '%Y-%m-%d'), '~', DATE_FORMAT(p.week_end, '%Y-%m-%d')) AS target_week,
        wn.nav_date,
        wn.target_nav / wn.previous_nav - 1 AS weekly_return
    FROM main_fund_universe fu
    JOIN week_nav wn
        ON wn.INNERCODE = fu.INNERCODE
    JOIN params p ON 1 = 1
    WHERE wn.target_nav IS NOT NULL
      AND wn.previous_nav IS NOT NULL
      AND wn.previous_nav <> 0
),
ranked AS (
    SELECT
        wr.*,
        RANK() OVER (ORDER BY wr.weekly_return DESC) AS return_rank,
        COUNT(*) OVER () AS peer_count
    FROM weekly_return wr
)
SELECT
    fund_code AS `基金代码`,
    fund_name AS `基金简称`,
    target_week AS `目标周`,
    DATE_FORMAT(nav_date, '%Y-%m-%d') AS `净值日期`,
    ROUND(weekly_return * 100, 2) AS `周收益(%)`,
    return_rank AS `排名`,
    peer_count AS `同类基金数量`
FROM ranked
WHERE fund_code = '519714';
