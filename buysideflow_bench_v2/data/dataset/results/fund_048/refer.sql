-- Extracted from fund_048/refer.py (main SQL: SQL)
-- Template: {{ name }} will be substituted from refer.py before execution.
-- DO NOT change the variable names here without updating refer.py.

WITH params AS (
    SELECT DATE('{{ AS_OF_DATE }}') AS as_of_date
),
fund_id AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCODE = '{{ FUND_CODE }}'
      AND SECUCATEGORY = 8
    LIMIT 1
),
bench_id AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCODE = '{{ BENCHMARK_CODE }}'
      AND SECUCATEGORY = 4
    LIMIT 1
),
nav_ranked AS (
    SELECT n.INNERCODE, n.TRADINGDAY, n.UNITNVRESTORED,
           ROW_NUMBER() OVER (PARTITION BY n.INNERCODE, n.TRADINGDAY
               ORDER BY n.INSERTTIME DESC, n.ID DESC) AS rn
    FROM mf_fundnetvalueretrans n
    JOIN fund_id f ON f.INNERCODE = n.INNERCODE
    CROSS JOIN params p
    WHERE n.TRADINGDAY <= p.as_of_date
      AND n.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
),
nav_visible AS (
    SELECT * FROM nav_ranked WHERE rn = 1 AND UNITNVRESTORED > 0
),
quarter_dates AS (
    SELECT
        CONCAT(YEAR(q.TRADINGDAY), 'Q', QUARTER(q.TRADINGDAY)) AS quarter_key,
        MAX(q.TRADINGDAY) AS quarter_end
    FROM qt_indexquote q
    JOIN bench_id b
      ON b.INNERCODE = q.INNERCODE
    CROSS JOIN fund_id f
    JOIN nav_visible n
      ON n.INNERCODE = f.INNERCODE AND n.TRADINGDAY = q.TRADINGDAY
    CROSS JOIN params p
    WHERE q.TRADINGDAY <= p.as_of_date
      AND q.CLOSEPRICE > 0 AND n.UNITNVRESTORED > 0
    GROUP BY CONCAT(YEAR(q.TRADINGDAY), 'Q', QUARTER(q.TRADINGDAY))
),
quarter_values AS (
    SELECT
        qd.quarter_key,
        qd.quarter_end,
        MAX(fn.UNITNVRESTORED) AS fund_nav,
        MAX(qi.CLOSEPRICE) AS bench_close
    FROM quarter_dates qd
    JOIN fund_id f
      ON 1 = 1
    JOIN bench_id b
      ON 1 = 1
    JOIN nav_visible fn
      ON fn.INNERCODE = f.INNERCODE
     AND fn.TRADINGDAY = qd.quarter_end
    JOIN qt_indexquote qi
      ON qi.INNERCODE = b.INNERCODE
     AND qi.TRADINGDAY = qd.quarter_end
    CROSS JOIN params p
    WHERE fn.UNITNVRESTORED IS NOT NULL
      AND qi.CLOSEPRICE IS NOT NULL
    GROUP BY qd.quarter_key, qd.quarter_end
),
quarter_returns AS (
    SELECT
        qv.quarter_key,
        qv.quarter_end,
        LAG(qv.quarter_key) OVER (ORDER BY qv.quarter_end) AS previous_quarter,
        qv.fund_nav / LAG(qv.fund_nav) OVER (ORDER BY qv.quarter_end) - 1 AS fund_qtr_ret,
        qv.bench_close / LAG(qv.bench_close) OVER (ORDER BY qv.quarter_end) - 1 AS bench_qtr_ret
    FROM quarter_values qv
),
scale_raw AS (
    SELECT
        CONCAT(YEAR(s.ENDDATE), 'Q', QUARTER(s.ENDDATE)) AS quarter_key,
        s.ENDDATE AS scale_date,
        s.NVI / 100000000.0 AS scale_yi,
        ROW_NUMBER() OVER (
            PARTITION BY CONCAT(YEAR(s.ENDDATE), 'Q', QUARTER(s.ENDDATE))
            ORDER BY s.ENDDATE DESC, s.INSERTTIME DESC, s.ID DESC
        ) AS rn
    FROM mf_scaleanalysis s
    JOIN fund_id f
      ON f.INNERCODE = s.INNERCODE
    CROSS JOIN params p
    WHERE s.ENDDATE <= p.as_of_date
      AND s.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND s.NVI IS NOT NULL
      AND MONTH(s.ENDDATE) IN (3, 6, 9, 12)
      AND DATE(s.ENDDATE) = LAST_DAY(s.ENDDATE)
),
scale_q AS (
    SELECT quarter_key, scale_date, scale_yi
    FROM scale_raw
    WHERE rn = 1
)
SELECT
    qr.quarter_key,
    DATE(qr.quarter_end) AS quarter_end,
    DATE(sq.scale_date) AS scale_date,
    sq.scale_yi,
    qr.fund_qtr_ret - qr.bench_qtr_ret AS excess_qtr_ret
FROM quarter_returns qr
JOIN scale_q sq
  ON sq.quarter_key = qr.quarter_key
WHERE qr.fund_qtr_ret IS NOT NULL
  AND qr.bench_qtr_ret IS NOT NULL
  AND qr.previous_quarter = CONCAT(
      YEAR(DATE_SUB(sq.scale_date, INTERVAL 3 MONTH)), 'Q',
      QUARTER(DATE_SUB(sq.scale_date, INTERVAL 3 MONTH)))
ORDER BY qr.quarter_end
