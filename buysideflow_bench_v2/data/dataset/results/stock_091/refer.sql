WITH
params AS (
    SELECT DATE('2025-07-31') AS as_of_date
),
trade_asof AS (
    SELECT MAX(t.TRADINGDATE) AS trade_day
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET IN (83, 90)
),
listing_cutoff AS (
    SELECT MIN(trading_day) AS eligible_list_date
    FROM (
        SELECT DISTINCT t.TRADINGDATE AS trading_day
        FROM qt_tradingdaynew t
        JOIN trade_asof ta
          ON t.TRADINGDATE <= ta.trade_day
        WHERE t.IFTRADINGDAY = 1
          AND t.SECUMARKET IN (83, 90)
        ORDER BY t.TRADINGDATE DESC
        LIMIT 250
    ) x
),
stock_candidates AS (
    SELECT
        sm.INNERCODE,
        sm.COMPANYCODE,
        sm.SECUCODE,
        sm.SECUABBR AS stock_name
    FROM secumain sm
    CROSS JOIN listing_cutoff lc
    WHERE sm.SECUCATEGORY = 1
          AND sm.SECUMARKET IN (83, 90)
          AND sm.LISTEDSECTOR IN (1, 2, 6)
          AND sm.LISTEDDATE <= lc.eligible_list_date
),
listing_ranked AS (
    SELECT ls.INNERCODE, ls.CHANGETYPE,
           ROW_NUMBER() OVER (
               PARTITION BY ls.INNERCODE
               ORDER BY ls.CHANGEDATE DESC, ls.INSERTTIME DESC, ls.JSID DESC, ls.ID DESC
           ) AS rn
    FROM lc_liststatus ls
    JOIN stock_candidates sc ON sc.INNERCODE = ls.INNERCODE
    CROSS JOIN trade_asof ta
    WHERE ls.SECUMARKET IN (83, 90)
      AND ls.CHANGEDATE < DATE_ADD(ta.trade_day, INTERVAL 1 DAY)
      AND ls.INSERTTIME < DATE_ADD(ta.trade_day, INTERVAL 1 DAY)
      AND ls.CHANGETYPE BETWEEN 1 AND 9
),
stock_base AS (
    SELECT sm.*
    FROM stock_candidates sm
    JOIN listing_ranked ls ON ls.INNERCODE = sm.INNERCODE AND ls.rn = 1
    WHERE ls.CHANGETYPE IN (1, 3, 6)
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              CROSS JOIN trade_asof ta
              WHERE ra.INNERCODE = sm.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND ra.IMPLEMENTDATE <= ta.trade_day
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE)
                    < DATE_ADD(ta.trade_day, INTERVAL 1 DAY)
                AND (
                    ra.REMOVEDATE IS NULL
                    OR ra.REMOVEDATE > ta.trade_day
                    OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE)
                        >= DATE_ADD(ta.trade_day, INTERVAL 1 DAY)
                )
          )
),
recent_20_trade_days AS (
    SELECT trading_day
    FROM (
        SELECT DISTINCT DATE(t.TRADINGDATE) AS trading_day
        FROM qt_tradingdaynew t CROSS JOIN trade_asof ta
        WHERE t.IFTRADINGDAY = 1 AND t.SECUMARKET IN (83, 90)
          AND t.TRADINGDATE <= ta.trade_day
        ORDER BY trading_day DESC LIMIT 20
    ) d
),
turnover_panel AS (
    SELECT sb.INNERCODE, d.trading_day,
           CASE WHEN q.TURNOVERVALUE >= 0 THEN q.TURNOVERVALUE
                WHEN q.TURNOVERVALUE IS NULL AND EXISTS (
                    SELECT 1 FROM lc_suspendresumption sr
                    WHERE sr.INNERCODE = sb.INNERCODE
                      AND DATE(sr.SUSPENDDATE) <= d.trading_day
                      AND COALESCE(sr.INFOPUBLDATE, sr.INSERTTIME)
                          < DATE_ADD(d.trading_day, INTERVAL 1 DAY)
                      AND TIME(sr.SUSPENDTIME) <= TIME('09:30:00')
                      AND (sr.RESUMPTIONDATE IS NULL
                           OR DATE(sr.RESUMPTIONDATE) > d.trading_day
                           OR (DATE(sr.RESUMPTIONDATE) = d.trading_day
                               AND TIME(sr.RESUMPTIONTIME) > TIME('15:00:00')))
                ) THEN 0 END AS turnover_value
    FROM stock_base sb CROSS JOIN recent_20_trade_days d
    LEFT JOIN qt_dailyquote q
      ON q.INNERCODE = sb.INNERCODE AND q.TRADINGDAY = d.trading_day
),
liquidity AS (
    SELECT INNERCODE, SUM(turnover_value) / 20 AS avg_turnover_value_20d,
           COUNT(turnover_value) AS quote_cnt_20d
    FROM turnover_panel GROUP BY INNERCODE
    HAVING COUNT(*) = 20 AND COUNT(turnover_value) = 20
       AND SUM(turnover_value) / 20 >= 30000000
),
market_asof AS (
    SELECT
        sb.INNERCODE,
        q.CLOSEPRICE
    FROM stock_base sb
    CROSS JOIN trade_asof ta
    JOIN qt_dailyquote q
      ON q.INNERCODE = sb.INNERCODE
     AND q.TRADINGDAY = ta.trade_day
    WHERE q.CLOSEPRICE IS NOT NULL
      AND q.CLOSEPRICE > 0
),
valuation_asof AS (
    SELECT
        v.INNERCODE,
        v.NEGOTIABLEMV
    FROM lc_dindicesforvaluation v
    JOIN (
        SELECT
            v.INNERCODE,
            MAX(v.JSID) AS max_jsid
        FROM lc_dindicesforvaluation v
        JOIN trade_asof ta
          ON v.TRADINGDAY = ta.trade_day
        JOIN stock_base sb
          ON sb.INNERCODE = v.INNERCODE
        GROUP BY v.INNERCODE
    ) k
      ON k.INNERCODE = v.INNERCODE
     AND k.max_jsid = v.JSID
),
base_sample AS (
    SELECT
        sb.INNERCODE,
        sb.COMPANYCODE,
        sb.SECUCODE,
        sb.stock_name,
        ma.CLOSEPRICE,
        va.NEGOTIABLEMV,
        PERCENT_RANK() OVER (ORDER BY ma.CLOSEPRICE ASC) AS price_rank
    FROM stock_base sb
    JOIN liquidity li
      ON li.INNERCODE = sb.INNERCODE
    JOIN market_asof ma
      ON ma.INNERCODE = sb.INNERCODE
    LEFT JOIN valuation_asof va
      ON va.INNERCODE = sb.INNERCODE
),
low_price_pool AS (
    SELECT *
    FROM base_sample
    WHERE price_rank <= 0.10
)
SELECT
    SECUCODE AS `股票代码`,
    stock_name AS `股票简称`,
    CLOSEPRICE AS `收盘价`,
    ROUND(NEGOTIABLEMV / 100000000, 2) AS `流通市值`
FROM low_price_pool
ORDER BY CLOSEPRICE ASC, SECUCODE ASC;
