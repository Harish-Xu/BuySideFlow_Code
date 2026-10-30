-- Extracted from fund_051/refer.py (main SQL: sql)
-- Template: {{ name }} will be substituted from refer.py before execution.
-- DO NOT change the variable names here without updating refer.py.

WITH params AS (
        SELECT
            DATE('{{ obs_date }}') AS obs_date,
            DATE_ADD(DATE('{{ obs_date }}'), INTERVAL 1 DAY) AS visible_cutoff
    ),
    calendar_dates AS (
        SELECT DATE(td.TRADINGDATE) AS trade_date
        FROM qt_tradingdaynew td
        JOIN params p
          ON td.TRADINGDATE <= p.obs_date
        WHERE td.SECUMARKET = 83
          AND td.IFTRADINGDAY = 1
        GROUP BY DATE(td.TRADINGDATE)
    ),
    ranked_calendar AS (
        SELECT
            trade_date,
            ROW_NUMBER() OVER (ORDER BY trade_date DESC) AS rn
        FROM calendar_dates
    ),
    price_dates AS (
        SELECT trade_date
        FROM ranked_calendar
        WHERE rn <= 253
    ),
    calendar_pairs AS (
        SELECT
            trade_date,
            LAG(trade_date) OVER (ORDER BY trade_date) AS previous_trade_date
        FROM price_dates
    ),
    return_dates AS (
        SELECT cp.trade_date, cp.previous_trade_date
        FROM calendar_pairs cp
        JOIN ranked_calendar rc
          ON rc.trade_date = cp.trade_date
         AND rc.rn <= 252
    ),
    a500_index AS (
        SELECT s.INNERCODE
        FROM secumain s
        WHERE s.SECUCATEGORY = 4
          AND s.SECUCODE = '000510'
        ORDER BY s.INNERCODE
        LIMIT 1
    ),
    fund_pool AS (
        SELECT
            f.INNERCODE,
            f.SECURITYCODE AS SECUCODE,
            sm.CHINAMEABBR
        FROM mf_fundarchives f
        JOIN secumain sm
          ON sm.INNERCODE = f.INNERCODE
         AND sm.SECUCATEGORY = 8
        JOIN params p
          ON 1 = 1
        WHERE f.INVESTMENTTYPE = 8
          AND f.SECURITYCODE = f.MAINCODE
          AND f.ESTABLISHMENTDATE <= DATE_SUB(p.obs_date, INTERVAL 1 YEAR)
          AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE > p.obs_date)
          AND (f.INSERTTIME IS NULL OR f.INSERTTIME < p.visible_cutoff)
          AND (
                f.PERFORMANCEBENCHMARK LIKE '%A500%增强%'
             OR sm.CHINAMEABBR LIKE '%A500%增强%'
          )
    ),
    fund_nav_ranked AS (
        SELECT
            fp.INNERCODE,
            fp.SECUCODE,
            fp.CHINAMEABBR,
            DATE(fr.TRADINGDAY) AS trade_date,
            fr.UNITNVRESTORED AS nav,
            ROW_NUMBER() OVER (
                PARTITION BY fp.INNERCODE, DATE(fr.TRADINGDAY)
                ORDER BY fr.INSERTTIME DESC, fr.UPDATETIME DESC, fr.ID DESC
            ) AS rn
        FROM fund_pool fp
        JOIN mf_fundnetvalueretrans fr
          ON fr.INNERCODE = fp.INNERCODE
        JOIN price_dates d
          ON d.trade_date = DATE(fr.TRADINGDAY)
        JOIN params p
          ON fr.INSERTTIME < p.visible_cutoff
        WHERE fr.UNITNVRESTORED > 0
    ),
    fund_nav AS (
        SELECT INNERCODE, SECUCODE, CHINAMEABBR, trade_date, nav
        FROM fund_nav_ranked
        WHERE rn = 1
    ),
    fund_return_all AS (
        SELECT
            INNERCODE,
            SECUCODE,
            CHINAMEABBR,
            trade_date,
            LAG(trade_date) OVER (PARTITION BY INNERCODE ORDER BY trade_date) AS previous_nav_date,
            nav / NULLIF(LAG(nav) OVER (PARTITION BY INNERCODE ORDER BY trade_date), 0) - 1 AS fund_ret
        FROM fund_nav
    ),
    fund_return AS (
        SELECT f.*
        FROM fund_return_all f
        JOIN return_dates d
          ON d.trade_date = f.trade_date
         AND d.previous_trade_date = f.previous_nav_date
        WHERE f.fund_ret IS NOT NULL
    ),
    index_price_ranked AS (
        SELECT
            DATE(i.TRADINGDAY) AS trade_date,
            i.CLOSEPRICE AS close_price,
            ROW_NUMBER() OVER (
                PARTITION BY DATE(i.TRADINGDAY)
                ORDER BY i.XGRQ DESC, i.ID DESC
            ) AS rn
        FROM qt_indexquote i
        JOIN a500_index ai
          ON ai.INNERCODE = i.INNERCODE
        JOIN price_dates d
          ON d.trade_date = DATE(i.TRADINGDAY)
        WHERE i.CLOSEPRICE > 0
    ),
    index_price AS (
        SELECT trade_date, close_price
        FROM index_price_ranked
        WHERE rn = 1
    ),
    index_return_all AS (
        SELECT
            trade_date,
            LAG(trade_date) OVER (ORDER BY trade_date) AS previous_index_date,
            close_price / NULLIF(LAG(close_price) OVER (ORDER BY trade_date), 0) - 1 AS index_ret
        FROM index_price
    ),
    index_return AS (
        SELECT i.*
        FROM index_return_all i
        JOIN return_dates d
          ON d.trade_date = i.trade_date
         AND d.previous_trade_date = i.previous_index_date
        WHERE i.index_ret IS NOT NULL
    ),
    aligned AS (
        SELECT
            f.INNERCODE,
            f.SECUCODE,
            f.CHINAMEABBR,
            f.trade_date,
            f.fund_ret - i.index_ret AS active_return
        FROM fund_return f
        JOIN index_return i
          ON i.trade_date = f.trade_date
    ),
    summary AS (
        SELECT
            a.INNERCODE,
            a.SECUCODE AS `主基金代码`,
            a.CHINAMEABBR AS `基金简称`,
            COUNT(*) AS `有效样本天数`,
            MIN(a.trade_date) AS `样本起始日期`,
            MAX(a.trade_date) AS `样本结束日期`,
            STDDEV_POP(a.active_return) * SQRT({{ ANNUAL_DAYS }}) AS tracking_error
        FROM aligned a
        GROUP BY a.INNERCODE, a.SECUCODE, a.CHINAMEABBR
    )
    SELECT
        p.obs_date AS `观察日期`,
        s.`主基金代码`,
        s.`基金简称`,
        ROUND(s.tracking_error, 4) AS `年化跟踪误差`,
        s.`有效样本天数`,
        s.`样本起始日期`,
        s.`样本结束日期`
    FROM summary s
    JOIN params p
      ON 1 = 1
    WHERE s.tracking_error > {{ TRACKING_ERROR_THRESHOLD }}
    ORDER BY s.tracking_error DESC, s.`主基金代码`
