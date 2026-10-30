-- name: trading_days
SELECT TRADINGDATE AS trade_date
FROM qt_tradingdaynew
WHERE SECUMARKET = 83
  AND IFTRADINGDAY = 1
  AND TRADINGDATE BETWEEN '$start_date' AND '$end_date'
ORDER BY TRADINGDATE;

-- name: factor_panel
WITH
schedule AS (
$schedule_rows
),
target_index AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '$index_code'
    ORDER BY INNERCODE
    LIMIT 1
),
active_components AS (
    SELECT DISTINCT
        s.obs_date,
        s.lookback_date,
        s.entry_date,
        s.month_end,
        ic.SECUINNERCODE AS INNERCODE
    FROM schedule s
    CROSS JOIN target_index idx
    JOIN lc_indexcomponent ic
      ON ic.INDEXINNERCODE = idx.INNERCODE
     AND ic.INDATE < DATE_ADD(s.obs_date, INTERVAL 1 DAY)
     AND (ic.OUTDATE IS NULL OR ic.OUTDATE >= DATE_ADD(s.obs_date, INTERVAL 1 DAY))
)
SELECT
    ac.obs_date,
    ac.INNERCODE,
    p_obs.BACKWARDPRICE / NULLIF(p_lag.BACKWARDPRICE, 0) - 1 AS trend_factor,
    p_end.BACKWARDPRICE /
        NULLIF(q_entry.OPENPRICE * p_entry.BACKWARDPRICE / NULLIF(q_entry.CLOSEPRICE, 0), 0)
        - 1 AS stock_return
FROM active_components ac
JOIN qt_performancedata p_lag
  ON p_lag.TRADINGDAY = ac.lookback_date
 AND p_lag.INNERCODE = ac.INNERCODE
JOIN qt_performancedata p_obs
  ON p_obs.TRADINGDAY = ac.obs_date
 AND p_obs.INNERCODE = ac.INNERCODE
JOIN qt_dailyquote q_entry
  ON q_entry.TRADINGDAY = ac.entry_date
 AND q_entry.INNERCODE = ac.INNERCODE
JOIN qt_performancedata p_entry
  ON p_entry.TRADINGDAY = ac.entry_date
 AND p_entry.INNERCODE = ac.INNERCODE
JOIN qt_performancedata p_end
  ON p_end.TRADINGDAY = ac.month_end
 AND p_end.INNERCODE = ac.INNERCODE
WHERE p_lag.BACKWARDPRICE > 0
  AND p_obs.BACKWARDPRICE > 0
  AND q_entry.OPENPRICE > 0
  AND q_entry.CLOSEPRICE > 0
  AND p_entry.BACKWARDPRICE > 0
  AND p_end.BACKWARDPRICE > 0
  AND NOT EXISTS (
      SELECT 1
      FROM cs_riskalert ra
      WHERE ra.INNERCODE = ac.INNERCODE
        AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
        AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) < DATE_ADD(ac.obs_date, INTERVAL 1 DAY)
        AND ra.IMPLEMENTDATE < DATE_ADD(ac.obs_date, INTERVAL 1 DAY)
        AND (
            ra.REMOVEDATE IS NULL
            OR ra.REMOVEDATE >= DATE_ADD(ac.obs_date, INTERVAL 1 DAY)
            OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.REMOVEDATE) >= DATE_ADD(ac.obs_date, INTERVAL 1 DAY)
        )
  )
  AND NOT EXISTS (
      SELECT 1
      FROM lc_suspendresumption sr
      WHERE sr.INNERCODE = ac.INNERCODE
        AND sr.SUSPENDDATE < DATE_ADD(ac.obs_date, INTERVAL 1 DAY)
        AND (
            sr.RESUMPTIONDATE IS NULL
            OR sr.RESUMPTIONDATE >= DATE_ADD(ac.obs_date, INTERVAL 1 DAY)
        )
  )
ORDER BY ac.obs_date, ac.INNERCODE;
