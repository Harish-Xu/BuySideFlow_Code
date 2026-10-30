-- name: price_panel
WITH
params AS (
    SELECT DATE('{as_of_date}') AS as_of_date
),
input_pool AS (
    {etf_pool_rows}
),
etf_pool AS (
    SELECT
        s.INNERCODE,
        ip.etf_code,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS etf_name
    FROM input_pool ip
    JOIN secumain s
      ON s.SECUCODE = ip.etf_code
    JOIN mf_fundarchives f
      ON f.INNERCODE = s.INNERCODE
    WHERE s.SECUCATEGORY = 8
      AND f.TYPE = 4
),
signal_asof AS (
    SELECT MAX(candidate_date) AS signal_date
    FROM (
        SELECT DATE(q.TRADINGDAY) AS candidate_date
        FROM etf_pool ep
        JOIN qt_fundsperformancehis q
          ON q.INNERCODE = ep.INNERCODE
        JOIN qt_tradingdaynew td
          ON DATE(td.TRADINGDATE) = DATE(q.TRADINGDAY)
         AND td.SECUMARKET = 83
         AND td.IFTRADINGDAY = 1
        JOIN params p
          ON DATE(q.TRADINGDAY) <= p.as_of_date
        WHERE q.CLOSEPRICE IS NOT NULL
          AND q.CLOSEPRICE > 0
        GROUP BY DATE(q.TRADINGDAY)
        HAVING COUNT(DISTINCT ep.etf_code) = {etf_count}
    ) d
),
next_trade_day AS (
    SELECT MIN(DATE(td.TRADINGDATE)) AS rebalance_date
    FROM qt_tradingdaynew td
    JOIN signal_asof sa
      ON DATE(td.TRADINGDATE) > sa.signal_date
    WHERE td.SECUMARKET = 83
      AND td.IFTRADINGDAY = 1
),
trade_days AS (
    SELECT DISTINCT DATE(td.TRADINGDATE) AS trading_day
    FROM qt_tradingdaynew td
    JOIN signal_asof sa
      ON DATE(td.TRADINGDATE) <= sa.signal_date
    WHERE td.SECUMARKET = 83
      AND td.IFTRADINGDAY = 1
),
trade_window AS (
    SELECT trading_day
    FROM (
        SELECT
            trading_day,
            ROW_NUMBER() OVER (ORDER BY trading_day DESC) AS rn_desc
        FROM trade_days
    ) t
    WHERE rn_desc <= {price_window}
)
SELECT
    DATE(sa.signal_date) AS signal_date,
    DATE(nt.rebalance_date) AS rebalance_date,
    ep.etf_code,
    ep.etf_name,
    tw.trading_day,
    q.CLOSEPRICE AS close_price
FROM trade_window tw
CROSS JOIN etf_pool ep
CROSS JOIN signal_asof sa
CROSS JOIN next_trade_day nt
LEFT JOIN qt_fundsperformancehis q
  ON q.INNERCODE = ep.INNERCODE
 AND DATE(q.TRADINGDAY) = tw.trading_day
 AND q.CLOSEPRICE > 0
ORDER BY ep.etf_code, tw.trading_day;
