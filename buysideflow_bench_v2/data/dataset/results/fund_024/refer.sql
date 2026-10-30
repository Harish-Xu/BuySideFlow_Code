WITH params AS (
    SELECT
        DATE('2024-04-08') AS anchor_date,
        DATE_SUB(DATE('2024-04-08'), INTERVAL 3 YEAR) AS start_date
),
target_fund AS (
    SELECT sm.INNERCODE
    FROM secumain sm
    WHERE sm.SECUCODE = '000001'
      AND sm.SECUCATEGORY = 8
    ORDER BY sm.INNERCODE
    LIMIT 1
),
fund_ret AS (
    SELECT
        n.TRADINGDAY,
        n.NVRDAILYGROWTHRATE / 100.0 AS fund_ret,
        ROW_NUMBER() OVER (
            PARTITION BY n.TRADINGDAY
            ORDER BY n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans n
    JOIN target_fund tf
      ON tf.INNERCODE = n.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE n.TRADINGDAY BETWEEN p.start_date AND p.anchor_date
      AND n.NVRDAILYGROWTHRATE IS NOT NULL
      AND n.INSERTTIME < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
),
bench_ret AS (
    SELECT
        q.TRADINGDAY,
        q.CHANGEPCT / 100.0 AS bench_ret,
        ROW_NUMBER() OVER (
            PARTITION BY q.TRADINGDAY
            ORDER BY q.XGRQ DESC, q.JSID DESC, q.ID DESC
        ) AS rn
    FROM qt_indexquote q
    JOIN secumain s
      ON s.INNERCODE = q.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE s.SECUCODE = '000300'
      AND s.SECUCATEGORY = 4
      AND q.TRADINGDAY BETWEEN p.start_date AND p.anchor_date
      AND q.CHANGEPCT IS NOT NULL
      AND q.CHANGEPCT < -3
      AND q.XGRQ < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
),
aligned_extreme_days AS (
    SELECT
        b.TRADINGDAY,
        b.bench_ret,
        f.fund_ret
    FROM bench_ret b
    JOIN fund_ret f
      ON f.TRADINGDAY = b.TRADINGDAY
    WHERE b.rn = 1
      AND f.rn = 1
)
SELECT
    COUNT(*) AS extreme_day_count,
    ROUND(AVG(bench_ret) * 100, 4) AS benchmark_avg_return_pct,
    ROUND(AVG(fund_ret) * 100, 4) AS fund_avg_return_pct,
    ROUND(AVG(fund_ret) / NULLIF(AVG(bench_ret), 0), 4) AS downside_capture_ratio
FROM aligned_extreme_days
HAVING COUNT(*) > 0;
