WITH params AS (
    SELECT
        DATE('2025-01-13') AS as_of_date,
        DATE('2024-01-01') AS start_date,
        DATE('2024-12-31') AS end_date,
        DATE_ADD(DATE('2025-01-13'), INTERVAL 1 DAY) AS visible_cutoff
),
target_fund AS (
    SELECT
        sm.INNERCODE
    FROM secumain sm
    JOIN params p
      ON 1 = 1
    WHERE sm.SECUCATEGORY = 8
      AND sm.SECUCODE = '673100'
      AND (sm.LISTEDDATE IS NULL OR sm.LISTEDDATE <= p.end_date)
    ORDER BY sm.INNERCODE ASC
    LIMIT 1
),
benchmark_index AS (
    SELECT
        sm.INNERCODE
    FROM secumain sm
    WHERE sm.SECUCATEGORY = 4
      AND sm.SECUCODE = '000300'
    ORDER BY sm.INNERCODE ASC
    LIMIT 1
),
fund_nav_dedup AS (
    SELECT
        TRADINGDAY,
        UNITNVRESTORED
    FROM (
        SELECT
            n.TRADINGDAY,
            n.UNITNVRESTORED,
            ROW_NUMBER() OVER (
                PARTITION BY n.TRADINGDAY
                ORDER BY n.INSERTTIME DESC, n.UPDATETIME DESC, n.ID DESC
            ) AS rn
        FROM mf_fundnetvalueretrans n
        JOIN target_fund tf
          ON tf.INNERCODE = n.INNERCODE
        JOIN params p
          ON n.TRADINGDAY <= p.end_date
         AND n.TRADINGDAY >= DATE_SUB(p.start_date, INTERVAL 15 DAY)
         AND n.INSERTTIME < p.visible_cutoff
        WHERE n.UNITNVRESTORED IS NOT NULL
          AND n.UNITNVRESTORED > 0
    ) x
    WHERE rn = 1
),
benchmark_quote_dedup AS (
    SELECT
        TRADINGDAY,
        CLOSEPRICE
    FROM (
        SELECT
            q.TRADINGDAY,
            q.CLOSEPRICE,
            ROW_NUMBER() OVER (
                PARTITION BY q.TRADINGDAY
                ORDER BY q.XGRQ DESC, q.ID DESC
            ) AS rn
        FROM qt_indexquote q
        JOIN benchmark_index bi
          ON bi.INNERCODE = q.INNERCODE
        JOIN params p
          ON q.TRADINGDAY <= p.end_date
         AND q.TRADINGDAY >= DATE_SUB(p.start_date, INTERVAL 15 DAY)
        WHERE q.CLOSEPRICE IS NOT NULL
          AND q.CLOSEPRICE > 0
    ) x
    WHERE rn = 1
),
common_level AS (
    SELECT
        f.TRADINGDAY,
        f.UNITNVRESTORED,
        b.CLOSEPRICE
    FROM fund_nav_dedup f
    JOIN benchmark_quote_dedup b
      ON b.TRADINGDAY = f.TRADINGDAY
),
common_return_all AS (
    SELECT
        TRADINGDAY,
        UNITNVRESTORED / NULLIF(LAG(UNITNVRESTORED) OVER (ORDER BY TRADINGDAY), 0) - 1 AS fund_daily_return,
        CLOSEPRICE / NULLIF(LAG(CLOSEPRICE) OVER (ORDER BY TRADINGDAY), 0) - 1 AS benchmark_daily_return
    FROM common_level
),
aligned_return AS (
    SELECT
        cr.TRADINGDAY,
        cr.fund_daily_return,
        cr.benchmark_daily_return,
        cr.fund_daily_return - cr.benchmark_daily_return AS excess_daily_return
    FROM common_return_all cr
    JOIN params p
      ON cr.TRADINGDAY BETWEEN p.start_date AND p.end_date
    WHERE cr.fund_daily_return IS NOT NULL
      AND cr.benchmark_daily_return IS NOT NULL
)
SELECT
    COUNT(*) AS `有效交易日数`,
    ROUND(AVG(CASE WHEN excess_daily_return > 0 THEN 1 ELSE 0 END) * 100, 2) AS `日胜率(%)`,
    ROUND(AVG(excess_daily_return) * 100, 2) AS `日均超额收益(%)`,
    ROUND((POWER(1 + AVG(excess_daily_return), 250) - 1) * 100, 2) AS `年化超额收益(%)`,
    ROUND(STDDEV_POP(excess_daily_return) * SQRT(252) * 100, 2) AS `跟踪误差(%)`
FROM aligned_return;
