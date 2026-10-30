WITH
params AS (
    SELECT DATE('2025-09-28') AS as_of_dt
),
target_index AS (
    SELECT s.INNERCODE
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND (s.SECUCODE = '000852'
           OR COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) = '中证1000')
    ORDER BY CASE WHEN s.SECUCODE = '000852' THEN 0 ELSE 1 END, s.INNERCODE
    LIMIT 1
),
base_series AS (
    SELECT
        q.TRADINGDAY,
        q.CLOSEPRICE,
        q.TURNOVERVALUE,
        AVG(q.CLOSEPRICE) OVER (
            ORDER BY q.TRADINGDAY
            ROWS BETWEEN 249 PRECEDING AND CURRENT ROW
        ) AS ma250,
        AVG(q.TURNOVERVALUE) OVER (
            ORDER BY q.TRADINGDAY
            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
        ) AS avg_turnover_20d,
        COUNT(q.TURNOVERVALUE) OVER (
            ORDER BY q.TRADINGDAY
            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
        ) AS turnover_obs_20d,
        ROW_NUMBER() OVER (
            ORDER BY q.TRADINGDAY
        ) AS rn
    FROM qt_indexquote q
    JOIN target_index i
      ON i.INNERCODE = q.INNERCODE
    JOIN params p
      ON q.TRADINGDAY <= p.as_of_dt
),
series AS (
    SELECT
        b.*,
        LAG(b.CLOSEPRICE) OVER (ORDER BY b.TRADINGDAY) AS prev_close,
        LAG(b.ma250) OVER (ORDER BY b.TRADINGDAY) AS prev_ma250
    FROM base_series b
),
break_day AS (
    SELECT
        TRADINGDAY AS break_date,
        rn AS break_rn,
        (CLOSEPRICE / ma250 - 1) * 100 AS break_depth_pct
    FROM series
    WHERE ma250 IS NOT NULL
      AND rn >= 250
      AND CLOSEPRICE < ma250
      AND (prev_close IS NULL OR prev_ma250 IS NULL OR prev_close >= prev_ma250)
),
recover_day AS (
    SELECT
        b.break_date,
        MIN(s.TRADINGDAY) AS recover_date
    FROM break_day b
    JOIN series s
      ON s.rn BETWEEN b.break_rn + 1 AND b.break_rn + 3
     AND s.CLOSEPRICE > s.ma250
    GROUP BY b.break_date
),
last_event AS (
    SELECT
        b.break_date,
        r.recover_date,
        b.break_depth_pct
    FROM break_day b
    JOIN recover_day r
      ON r.break_date = b.break_date
    ORDER BY b.break_date DESC
    LIMIT 1
)
SELECT
    '中证1000' AS `指数`,
    le.break_date AS `跌破日`,
    le.recover_date AS `收回日`,
    ROUND(le.break_depth_pct, 4) AS `跌破深度(%)`,
    ROUND(s.TURNOVERVALUE / NULLIF(s.avg_turnover_20d, 0), 4) AS `成交额放大比例`
FROM last_event le
JOIN series s
  ON s.TRADINGDAY = le.recover_date
WHERE s.turnover_obs_20d = 20;
