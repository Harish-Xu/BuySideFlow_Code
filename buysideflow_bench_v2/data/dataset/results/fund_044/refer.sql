WITH params AS (
    SELECT
        DATE('2023-01-01') AS start_date,
        DATE('2025-11-28') AS as_of_date,
        -- Explicit observation timestamp in Beijing time.
        '2025-11-28 09:00:00' AS visible_cutoff
),
fund_list AS (
    SELECT 1 AS input_order, '005739' AS fund_code
),
target_funds AS (
    SELECT
        fl.input_order,
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        sm.SECUABBR AS fund_name
    FROM fund_list fl
    JOIN mf_fundarchives fa
        ON fa.SECURITYCODE = fl.fund_code
    JOIN secumain sm
        ON sm.INNERCODE = fa.INNERCODE
       AND sm.SECUCATEGORY = 8
),
nav_latest AS (
    SELECT *
    FROM (
        SELECT
            tf.input_order,
            tf.INNERCODE,
            tf.fund_code,
            tf.fund_name,
            DATE(fn.TRADINGDAY) AS TRADINGDAY,
            fn.UNITNVRESTORED AS nav,
            ROW_NUMBER() OVER (
                PARTITION BY tf.INNERCODE, DATE(fn.TRADINGDAY)
                ORDER BY fn.INSERTTIME DESC, fn.UPDATETIME DESC, fn.ID DESC
            ) AS rn
        FROM target_funds tf
        JOIN mf_fundnetvalueretrans fn
            ON fn.INNERCODE = tf.INNERCODE
        JOIN params p ON 1 = 1
        WHERE fn.TRADINGDAY >= p.start_date
          AND fn.TRADINGDAY < p.as_of_date
          AND fn.INSERTTIME <= p.visible_cutoff
    ) x
    WHERE rn = 1 AND nav > 0
),
running_peak AS (
    SELECT
        nl.*,
        MAX(nl.nav) OVER (
            PARTITION BY nl.INNERCODE
            ORDER BY nl.TRADINGDAY
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS peak_nav
    FROM nav_latest nl
),
underwater_flag AS (
    SELECT
        rp.*,
        CASE WHEN rp.nav < rp.peak_nav THEN 1 ELSE 0 END AS is_underwater
    FROM running_peak rp
),
underwater_group AS (
    SELECT
        uf.*,
        SUM(CASE WHEN uf.is_underwater = 0 THEN 1 ELSE 0 END) OVER (
            PARTITION BY uf.INNERCODE
            ORDER BY uf.TRADINGDAY
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS group_id
    FROM underwater_flag uf
),
underwater_period AS (
    SELECT
        input_order,
        INNERCODE,
        fund_code,
        fund_name,
        group_id,
        MIN(TRADINGDAY) AS start_date,
        MAX(TRADINGDAY) AS last_underwater_date,
        MIN(nav / NULLIF(peak_nav, 0) - 1) AS max_drawdown
    FROM underwater_group
    WHERE is_underwater = 1
    GROUP BY input_order, INNERCODE, fund_code, fund_name, group_id
),
period_with_recovery AS (
    SELECT
        up.*,
        MIN(ug.TRADINGDAY) AS recovery_date
    FROM underwater_period up
    LEFT JOIN underwater_group ug
        ON ug.INNERCODE = up.INNERCODE
       AND ug.is_underwater = 0
       AND ug.group_id = up.group_id + 1
       AND ug.TRADINGDAY > up.last_underwater_date
    GROUP BY
        up.input_order,
        up.INNERCODE,
        up.fund_code,
        up.fund_name,
        up.group_id,
        up.start_date,
        up.last_underwater_date,
        up.max_drawdown
),
period_lengths AS (
    SELECT
        pwr.*,
        COALESCE(pwr.recovery_date, p.as_of_date) AS end_date,
        DATEDIFF(COALESCE(pwr.recovery_date, p.as_of_date), pwr.start_date)
            AS calendar_days
    FROM period_with_recovery pwr
    JOIN params p ON 1 = 1
),
ranked AS (
    SELECT
        pl.*,
        ROW_NUMBER() OVER (
            PARTITION BY pl.INNERCODE
            ORDER BY pl.calendar_days DESC, pl.start_date ASC
        ) AS rn
    FROM period_lengths pl
),
funds_with_nav AS (
    SELECT DISTINCT input_order, INNERCODE, fund_code, fund_name
    FROM nav_latest
)
SELECT
    f.fund_code AS `基金代码`,
    f.fund_name AS `基金简称`,
    DATE_FORMAT(r.start_date, '%Y-%m-%d') AS `开始日`,
    DATE_FORMAT(r.end_date, '%Y-%m-%d') AS `结束日`,
    COALESCE(r.calendar_days, 0) AS `持续自然日数`,
    ROUND(COALESCE(r.max_drawdown, 0) * 100, 2) AS `区间最大回撤`
FROM funds_with_nav f
LEFT JOIN ranked r ON r.INNERCODE = f.INNERCODE AND r.rn = 1
ORDER BY f.input_order;
