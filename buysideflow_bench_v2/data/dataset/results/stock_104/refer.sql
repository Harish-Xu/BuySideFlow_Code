WITH
params AS (
    SELECT
        DATE('2025-12-31') AS as_of_date,
        DATE_ADD(DATE('2025-12-31'), INTERVAL 1 DAY) AS visible_before
),
target_index AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '000300'
    ORDER BY INNERCODE
    LIMIT 1
),
past_trade_days AS (
    SELECT
        TRADINGDATE,
        ROW_NUMBER() OVER (ORDER BY TRADINGDATE DESC) AS rn_desc
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET = 83
),
future_trade_days AS (
    SELECT
        TRADINGDATE,
        ROW_NUMBER() OVER (ORDER BY TRADINGDATE) AS rn_asc
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE > p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET = 83
),
trade_bounds AS (
    SELECT
        MAX(CASE WHEN rn_desc = 1 THEN TRADINGDATE END) AS obs_trade_day,
        MAX(CASE WHEN rn_desc = 100 THEN TRADINGDATE END) AS hist_start_day
    FROM past_trade_days
),
exit_day AS (
    SELECT TRADINGDATE AS exit_trade_day
    FROM future_trade_days
    WHERE rn_asc = 20
),
constituents AS (
    SELECT DISTINCT
        ic.SECUINNERCODE AS innercode
    FROM lc_indexcomponent ic
    JOIN target_index ti
      ON ti.INNERCODE = ic.INDEXINNERCODE
    JOIN trade_bounds tb
      ON ic.INDATE <= tb.obs_trade_day
    WHERE ic.OUTDATE IS NULL OR ic.OUTDATE > tb.obs_trade_day
),
stock_base AS (
    SELECT
        sm.INNERCODE,
        sm.SECUCODE,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, sm.SECUCODE) AS stock_name,
        sm.LISTEDSECTOR
    FROM constituents c
    JOIN secumain sm
      ON sm.INNERCODE = c.innercode
    WHERE sm.SECUCATEGORY = 1
      AND sm.SECUMARKET IN (83, 90)
),
active_risk_alert AS (
    SELECT DISTINCT sb.INNERCODE
    FROM stock_base sb
    JOIN cs_riskalert ra
      ON ra.INNERCODE = sb.INNERCODE
    JOIN trade_bounds tb
      ON DATE(COALESCE(ra.IMPLANNOUCEDATE, ra.IMPLEMENTDATE)) <= tb.obs_trade_day
     AND DATE(ra.IMPLEMENTDATE) <= tb.obs_trade_day
    JOIN params p
      ON COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) < p.visible_before
    WHERE ra.RISKALERTTYPE IN (1, 5, 12, 14)
      AND (
          ra.REMOVEDATE IS NULL
          OR DATE(ra.REMOVEDATE) > tb.obs_trade_day
          OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) >= p.visible_before
      )
),
quote_history AS (
    SELECT
        sb.INNERCODE,
        q.TRADINGDAY,
        q.CLOSEPRICE,
        q.TURNOVERVOLUME,
        q.TURNOVERVALUE,
        AVG(q.CLOSEPRICE) OVER (
            PARTITION BY sb.INNERCODE
            ORDER BY q.TRADINGDAY
            ROWS BETWEEN 99 PRECEDING AND CURRENT ROW
        ) AS ma100,
        COUNT(q.CLOSEPRICE) OVER (
            PARTITION BY sb.INNERCODE
            ORDER BY q.TRADINGDAY
            ROWS BETWEEN 99 PRECEDING AND CURRENT ROW
        ) AS ma100_obs,
        ROW_NUMBER() OVER (
            PARTITION BY sb.INNERCODE
            ORDER BY q.TRADINGDAY DESC
        ) AS rn_desc
    FROM stock_base sb
    JOIN qt_dailyquote q
      ON q.INNERCODE = sb.INNERCODE
    JOIN trade_bounds tb
      ON q.TRADINGDAY BETWEEN tb.hist_start_day AND tb.obs_trade_day
    WHERE q.CLOSEPRICE > 0
),
obs_quote AS (
    SELECT
        qh.INNERCODE,
        qh.CLOSEPRICE AS obs_close,
        qh.ma100
    FROM quote_history qh
    JOIN trade_bounds tb
      ON qh.TRADINGDAY = tb.obs_trade_day
    WHERE qh.rn_desc = 1
      AND qh.ma100_obs = 100
      AND qh.CLOSEPRICE > qh.ma100
      AND qh.TURNOVERVOLUME > 0
      AND qh.TURNOVERVALUE > 0
),
return_prices AS (
    SELECT
        sb.INNERCODE,
        p0.BACKWARDPRICE AS obs_backward_price,
        p90.BACKWARDPRICE AS backward_price_90d_ago,
        p20.BACKWARDPRICE AS exit_backward_price
    FROM stock_base sb
    CROSS JOIN trade_bounds tb
    CROSS JOIN exit_day ed
    JOIN past_trade_days ptd90
      ON ptd90.rn_desc = 91
    JOIN qt_performancedata p0
      ON p0.INNERCODE = sb.INNERCODE
     AND p0.TRADINGDAY = tb.obs_trade_day
    JOIN qt_performancedata p90
      ON p90.INNERCODE = sb.INNERCODE
     AND p90.TRADINGDAY = ptd90.TRADINGDATE
    LEFT JOIN qt_performancedata p20
      ON p20.INNERCODE = sb.INNERCODE
     AND p20.TRADINGDAY = ed.exit_trade_day
     AND p20.BACKWARDPRICE > 0
    WHERE p0.BACKWARDPRICE > 0
      AND p90.BACKWARDPRICE > 0
),
benchmark_return AS (
    SELECT
        i20.CLOSEPRICE / NULLIF(i0.CLOSEPRICE, 0) - 1.0 AS hs300_return_20d
    FROM target_index ti
    CROSS JOIN trade_bounds tb
    CROSS JOIN exit_day ed
    JOIN qt_indexquote i0
      ON i0.INNERCODE = ti.INNERCODE
     AND i0.TRADINGDAY = tb.obs_trade_day
    JOIN qt_indexquote i20
      ON i20.INNERCODE = ti.INNERCODE
     AND i20.TRADINGDAY = ed.exit_trade_day
    WHERE i0.CLOSEPRICE > 0
      AND i20.CLOSEPRICE > 0
),
ranked AS (
    SELECT
        sb.SECUCODE,
        sb.stock_name,
        rp.obs_backward_price / rp.backward_price_90d_ago - 1.0 AS recent_90d_return,
        rp.exit_backward_price / rp.obs_backward_price - 1.0 AS stock_forward_20d_return,
        br.hs300_return_20d,
        ROW_NUMBER() OVER (
            ORDER BY rp.obs_backward_price / rp.backward_price_90d_ago - 1.0 DESC,
                     sb.SECUCODE
        ) AS ret_rank
    FROM stock_base sb
    JOIN obs_quote oq
      ON oq.INNERCODE = sb.INNERCODE
    JOIN return_prices rp
      ON rp.INNERCODE = sb.INNERCODE
    LEFT JOIN benchmark_return br
      ON 1 = 1
    LEFT JOIN active_risk_alert ara
      ON ara.INNERCODE = sb.INNERCODE
    WHERE ara.INNERCODE IS NULL
)
SELECT
    SECUCODE AS `股票代码`,
    stock_name AS `股票简称`,
    ROUND(recent_90d_return * 100.0, 2) AS `近90日收益(%)`,
    ROUND((stock_forward_20d_return - hs300_return_20d) * 100.0, 2) AS `未来20日相对沪深300超额收益(%)`
FROM ranked
WHERE ret_rank <= 10
ORDER BY recent_90d_return DESC, SECUCODE;
