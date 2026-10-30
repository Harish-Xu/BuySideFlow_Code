WITH params AS (
    SELECT DATE('2025-12-15') AS as_of_date
),
latest_trade AS (
    SELECT MAX(TRADINGDATE) AS trading_day
    FROM qt_tradingdaynew
    WHERE IFTRADINGDAY = 1
      AND SECUMARKET = 83
      AND TRADINGDATE < (SELECT as_of_date FROM params)
),
week_ago_trade AS (
    SELECT MAX(TRADINGDATE) AS trading_day
    FROM qt_tradingdaynew
    WHERE IFTRADINGDAY = 1
      AND SECUMARKET = 83
      AND TRADINGDATE <= DATE_SUB((SELECT trading_day FROM latest_trade), INTERVAL 7 DAY)
),
industry_info AS (
    SELECT
        libi.INDEXCODE AS innercode,
        sm.SECUCODE AS index_code,
        sm.SECUABBR AS index_name
    FROM lc_indexbasicinfo libi
    JOIN secumain sm
      ON sm.INNERCODE = libi.INDEXCODE
    CROSS JOIN latest_trade lt
    WHERE libi.PUBINDEXTYPE = 121101
      AND libi.INDUSTRYSTANDARD = 38
      AND libi.PUBDATE <= lt.trading_day
      AND (libi.ENDDATE IS NULL OR libi.ENDDATE > lt.trading_day)
),
weekly_performance AS (
    SELECT
        ii.index_code,
        ii.index_name,
        (end_q.CLOSEPRICE / NULLIF(start_q.CLOSEPRICE, 0) - 1) * 100 AS change_pct
    FROM industry_info ii
    JOIN qt_indexquote end_q
      ON end_q.INNERCODE = ii.innercode
     AND end_q.TRADINGDAY = (SELECT trading_day FROM latest_trade)
    JOIN qt_indexquote start_q
      ON start_q.INNERCODE = ii.innercode
     AND start_q.TRADINGDAY = (SELECT trading_day FROM week_ago_trade)
    WHERE end_q.CLOSEPRICE IS NOT NULL
      AND start_q.CLOSEPRICE IS NOT NULL
      AND start_q.CLOSEPRICE > 0
),
ranked_perf AS (
    SELECT
        index_code,
        index_name,
        change_pct,
        ROW_NUMBER() OVER (ORDER BY change_pct DESC, index_code) AS gain_rank,
        ROW_NUMBER() OVER (ORDER BY change_pct ASC, index_code) AS loss_rank
    FROM weekly_performance
)
SELECT
    p.as_of_date AS meeting_date,
    rp.index_code,
    rp.index_name,
    ROUND(rp.change_pct, 4) AS change_pct
FROM ranked_perf rp
CROSS JOIN params p
WHERE rp.gain_rank <= 3 OR rp.loss_rank <= 3
ORDER BY change_pct DESC, index_code;
