WITH
params AS (
    SELECT
        DATE('2024-01-01') AS start_date,
        DATE('2025-12-31') AS end_date,
        DATE_ADD(DATE('2025-12-31'), INTERVAL 200 DAY) AS future_end_date
),
target_index AS (
    SELECT s.INNERCODE
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE = '000300'
    ORDER BY s.INNERCODE
    LIMIT 1
),
trading_calendar AS (
    SELECT
        tradingday,
        ROW_NUMBER() OVER (ORDER BY tradingday) AS trade_seq
    FROM (
        SELECT DISTINCT t.TRADINGDATE AS tradingday
        FROM qt_tradingdaynew t
        JOIN params p
          ON t.TRADINGDATE BETWEEN p.start_date AND p.future_end_date
        WHERE t.IFTRADINGDAY = 1
          AND t.SECUMARKET IN (83, 90)
    ) d
),
month_end AS (
    SELECT
        MAX(tc.tradingday) AS base_date
    FROM trading_calendar tc
    JOIN params p
      ON tc.tradingday BETWEEN p.start_date AND p.end_date
    GROUP BY YEAR(tc.tradingday), MONTH(tc.tradingday)
),
analysis_calendar AS (
    SELECT
        base.tradingday AS base_date,
        base.trade_seq AS base_seq,
        future.tradingday AS future_date
    FROM month_end me
    JOIN trading_calendar base
      ON base.tradingday = me.base_date
    JOIN trading_calendar future
      ON future.trade_seq = base.trade_seq + 63
),
hs300_stocks AS (
    SELECT DISTINCT
        ac.base_date,
        ac.future_date,
        ic.SECUINNERCODE AS innercode
    FROM analysis_calendar ac
    JOIN target_index ti
    JOIN lc_indexcomponent ic
      ON ic.INDEXINNERCODE = ti.INNERCODE
     AND ic.INDATE <= ac.base_date
     AND (ic.OUTDATE IS NULL OR ic.OUTDATE > ac.base_date)
    JOIN secumain sm
      ON sm.INNERCODE = ic.SECUINNERCODE
    WHERE sm.SECUCATEGORY = 1
      AND sm.SECUMARKET IN (83, 90)
),
base_factor AS (
    SELECT
        hs.base_date,
        hs.future_date,
        v.INNERCODE,
        1.0 / v.PE AS ep_factor
    FROM hs300_stocks hs
    JOIN lc_dindicesforvaluation v
      ON v.INNERCODE = hs.innercode
     AND v.TRADINGDAY = hs.base_date
    WHERE v.PE > 0
      AND v.PE <= 100
),
base_price AS (
    SELECT
        hs.base_date,
        p.INNERCODE,
        p.BACKWARDPRICE AS backward_price
    FROM hs300_stocks hs
    JOIN qt_performancedata p
      ON p.INNERCODE = hs.innercode
     AND p.TRADINGDAY = hs.base_date
    WHERE p.BACKWARDPRICE > 0
),
future_price AS (
    SELECT
        hs.base_date,
        p.INNERCODE,
        p.BACKWARDPRICE AS backward_price
    FROM hs300_stocks hs
    JOIN qt_performancedata p
      ON p.INNERCODE = hs.innercode
     AND p.TRADINGDAY = hs.future_date
    WHERE p.BACKWARDPRICE > 0
)
SELECT
    bf.base_date AS tradingday,
    bf.INNERCODE AS innercode,
    bf.ep_factor,
    fp.backward_price / bp.backward_price - 1 AS return_63d
FROM base_factor bf
JOIN base_price bp
  ON bp.INNERCODE = bf.INNERCODE
 AND bp.base_date = bf.base_date
JOIN future_price fp
  ON fp.INNERCODE = bf.INNERCODE
 AND fp.base_date = bf.base_date
WHERE ABS(fp.backward_price / bp.backward_price - 1) <= 0.5;
