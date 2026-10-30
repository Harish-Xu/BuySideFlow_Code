-- name: pmi_values
SELECT
    m.INDICATORCODE AS indicator_code,
    DATE(m.ENDDATE) AS month_end,
    m.INFOPUBLDATE AS publish_time,
    m.DATAVALUE AS indicator_value,
    m.UPDATETIME AS update_time,
    m.JSID AS jsid,
    m.ID AS record_id
FROM c_ed_macroindicatordata m
WHERE m.INDICATORCODE IN ($production_code, $new_order_code)
  AND m.ENDDATE >= DATE('$history_start_month')
  AND m.ENDDATE < DATE('$month_after_asof')
  AND m.INFOPUBLDATE < TIMESTAMP('$next_day_ts')
ORDER BY m.INDICATORCODE, m.ENDDATE, m.INFOPUBLDATE, m.UPDATETIME, m.JSID, m.ID

-- name: trading_days
SELECT DISTINCT DATE(TRADINGDATE) AS trade_date
FROM qt_tradingdaynew
WHERE IFTRADINGDAY = 1
  AND SECUMARKET IN (83, 90)
  AND TRADINGDATE BETWEEN DATE('$calendar_start_date') AND DATE('$calendar_end_date')
ORDER BY trade_date

-- name: indices
SELECT
    sm.INNERCODE AS inner_code,
    sm.SECUCODE AS index_code,
    sm.SECUABBR AS index_name,
    sm.CHINAME AS full_name
FROM secumain sm
WHERE sm.SECUCATEGORY = 4
  AND sm.SECUMARKET IN (83, 84, 90)
  AND sm.SECUCODE IN ($index_code_values)
ORDER BY
    CASE WHEN sm.SECUCODE = '$benchmark_code' THEN 0 ELSE 1 END,
    sm.SECUCODE

-- name: index_quotes
SELECT
    q.INNERCODE AS inner_code,
    DATE(q.TRADINGDAY) AS trade_date,
    q.CLOSEPRICE AS close_price
FROM qt_indexquote q
WHERE q.INNERCODE IN ($inner_code_values)
  AND q.TRADINGDAY IN ($date_values)
  AND q.CLOSEPRICE IS NOT NULL
  AND q.CLOSEPRICE > 0
