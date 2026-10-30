WITH params AS (
    SELECT DATE('2025-04-30') AS as_of_date
),
target_fund AS (
    SELECT
        fa.INNERCODE,
        fa.SECUCODE
    FROM mf_fundarchives fa
    WHERE fa.SECUCODE = '010624'
),
nav_ranked AS (
    SELECT
        fn.TRADINGDAY,
        fn.UNITNVRESTORED AS nav,
        DATE_FORMAT(fn.TRADINGDAY, '%Y-%m') AS calendar_month,
        ROW_NUMBER() OVER (
            PARTITION BY fn.TRADINGDAY
            ORDER BY fn.INSERTTIME DESC, fn.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans fn
    JOIN target_fund tf ON tf.INNERCODE = fn.INNERCODE
    JOIN params p
      ON fn.TRADINGDAY <= p.as_of_date
    WHERE fn.UNITNVRESTORED IS NOT NULL
      AND fn.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
),
nav_month AS (
    SELECT TRADINGDAY, nav, calendar_month
    FROM nav_ranked
    WHERE rn = 1
),
nav_ordered AS (
    SELECT
        nm.*,
        LAG(nm.nav) OVER (ORDER BY nm.TRADINGDAY) AS previous_nav,
        ROW_NUMBER() OVER (
            PARTITION BY nm.calendar_month
            ORDER BY nm.TRADINGDAY
        ) AS month_rn
    FROM nav_month nm
),
month_seed AS (
    SELECT
        calendar_month,
        MAX(CASE WHEN month_rn = 1 THEN previous_nav END) AS start_nav
    FROM nav_ordered
    GROUP BY calendar_month
),
dd_base AS (
    SELECT
        nm.calendar_month,
        nm.TRADINGDAY,
        nm.nav,
        GREATEST(
            COALESCE(ms.start_nav, nm.nav),
            MAX(nm.nav) OVER (
                PARTITION BY nm.calendar_month
                ORDER BY nm.TRADINGDAY
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            )
        ) AS peak_nav
    FROM nav_ordered nm
    JOIN month_seed ms
      ON ms.calendar_month = nm.calendar_month
),
dd_detail AS (
    SELECT
        db.calendar_month,
        db.TRADINGDAY,
        db.nav,
        db.peak_nav,
        (db.peak_nav - db.nav) / NULLIF(db.peak_nav, 0) AS drawdown
    FROM dd_base db
),
month_max AS (
    SELECT
        dd.calendar_month,
        dd.drawdown,
        ROW_NUMBER() OVER (PARTITION BY dd.calendar_month ORDER BY dd.drawdown DESC) AS rn
    FROM dd_detail dd
)
SELECT
    mm.calendar_month AS 月份,
    ROUND(mm.drawdown * 100, 4) AS 最大回撤百分比
FROM month_max mm
WHERE mm.rn = 1
ORDER BY mm.drawdown DESC
LIMIT 1;
