-- name: schedule
WITH trading_calendar AS (
    SELECT
        trade_date,
        ROW_NUMBER() OVER (ORDER BY trade_date) AS trade_no
    FROM (
        SELECT DISTINCT DATE(TRADINGDATE) AS trade_date
        FROM qt_tradingdaynew
        WHERE IFTRADINGDAY = 1
          AND SECUMARKET IN (83, 90)
          AND TRADINGDATE >= '2025-01-01'
          AND TRADINGDATE < '2025-09-01'
    ) t
),
month_ends AS (
    SELECT MAX(trade_date) AS observation_date
    FROM trading_calendar
    WHERE trade_date <= '2025-06-30'
    GROUP BY YEAR(trade_date), MONTH(trade_date)
)
SELECT
    m.observation_date,
    future.trade_date AS future_date
FROM month_ends m
JOIN trading_calendar current_day
  ON current_day.trade_date = m.observation_date
JOIN trading_calendar future
  ON future.trade_no = current_day.trade_no + 20
ORDER BY m.observation_date;

-- name: monthly_result
WITH params AS (
    SELECT DATE('$obs_date') AS observation_date, DATE('$future_date') AS future_date
),
target_index AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '399006'
    ORDER BY INNERCODE
    LIMIT 1
),
components AS (
    SELECT DISTINCT
        s.INNERCODE,
        s.COMPANYCODE,
        LPAD(s.SECUCODE, 6, '0') AS stock_code
    FROM params p
    JOIN target_index ti
      ON 1 = 1
    JOIN lc_indexcomponent ic
      ON ic.INDEXINNERCODE = ti.INNERCODE
     AND ic.INDATE <= p.observation_date
     AND (ic.OUTDATE IS NULL OR ic.OUTDATE > p.observation_date)
    JOIN secumain s
      ON s.INNERCODE = ic.SECUINNERCODE
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = s.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND ra.IMPLEMENTDATE < DATE_ADD(p.observation_date, INTERVAL 1 DAY)
            AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE)
                < DATE_ADD(p.observation_date, INTERVAL 1 DAY)
            AND (
                ra.REMOVEDATE IS NULL
                OR ra.REMOVEDATE > p.observation_date
                OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.REMOVEDATE) > p.observation_date
            )
      )
),
quote_ranked AS (
    SELECT
        q.INNERCODE,
        q.CLOSEPRICE,
        q.TURNOVERVALUE,
        ROW_NUMBER() OVER (
            PARTITION BY q.INNERCODE
            ORDER BY q.JSID DESC, q.ID DESC
        ) AS rn
    FROM params p
    JOIN components c
      ON 1 = 1
    JOIN qt_dailyquote q
      ON q.INNERCODE = c.INNERCODE
     AND q.TRADINGDAY >= p.observation_date
     AND q.TRADINGDAY < DATE_ADD(p.observation_date, INTERVAL 1 DAY)
),
valuation_ranked AS (
    SELECT
        v.INNERCODE,
        v.PE AS pe_ttm,
        ROW_NUMBER() OVER (
            PARTITION BY v.INNERCODE
            ORDER BY v.JSID DESC, v.ID DESC
        ) AS rn
    FROM params p
    JOIN components c
      ON 1 = 1
    JOIN lc_dindicesforvaluation v
      ON v.INNERCODE = c.INNERCODE
     AND v.TRADINGDAY >= p.observation_date
     AND v.TRADINGDAY < DATE_ADD(p.observation_date, INTERVAL 1 DAY)
),
financial_ranked AS (
    SELECT
        f.COMPANYCODE,
        f.OPERATINGREVENUEYOY AS revenue_yoy,
        f.NPPARENTCOMPANYYOY AS parent_np_yoy,
        ROW_NUMBER() OVER (
            PARTITION BY f.COMPANYCODE
            ORDER BY
                f.ENDDATE DESC,
                CASE f.MARK
                    WHEN 1 THEN 1
                    WHEN 8 THEN 2
                    WHEN 7 THEN 3
                    WHEN 6 THEN 4
                    WHEN 2 THEN 5
                    ELSE 99
                END,
                f.INFOPUBLDATE DESC,
                COALESCE(f.INSERTTIME, f.INFOPUBLDATE) DESC,
                f.JSID DESC,
                f.ID DESC
        ) AS rn
    FROM params p
    JOIN (SELECT DISTINCT COMPANYCODE FROM components) c
      ON 1 = 1
    JOIN fin_derivative f
      ON f.COMPANYCODE = c.COMPANYCODE
     AND f.ENDDATE < DATE_ADD(p.observation_date, INTERVAL 1 DAY)
     AND f.INFOPUBLDATE < DATE_ADD(p.observation_date, INTERVAL 1 DAY)
     AND (f.INSERTTIME IS NULL OR f.INSERTTIME < DATE_ADD(p.observation_date, INTERVAL 1 DAY))
    WHERE f.MARK IN (1, 8, 7, 6, 2)
),
eligible AS (
    SELECT
        c.INNERCODE,
        c.stock_code,
        v.pe_ttm
    FROM components c
    JOIN quote_ranked q
      ON q.INNERCODE = c.INNERCODE
     AND q.rn = 1
    JOIN valuation_ranked v
      ON v.INNERCODE = c.INNERCODE
     AND v.rn = 1
    JOIN financial_ranked f
      ON f.COMPANYCODE = c.COMPANYCODE
     AND f.rn = 1
    WHERE q.CLOSEPRICE > 0
      AND q.TURNOVERVALUE > 0
      AND v.pe_ttm > 0
      AND f.revenue_yoy > 0
      AND f.parent_np_yoy > 0
),
grouped AS (
    SELECT
        e.*,
        NTILE(5) OVER (
            ORDER BY e.pe_ttm ASC, e.stock_code ASC
        ) AS pe_group
    FROM eligible e
),
performance_ranked AS (
    SELECT
        p.INNERCODE,
        DATE(p.TRADINGDAY) AS trade_date,
        p.BACKWARDPRICE AS adj_close,
        ROW_NUMBER() OVER (
            PARTITION BY p.INNERCODE, DATE(p.TRADINGDAY)
            ORDER BY p.UPDATETIME DESC, p.JSID DESC, p.ID DESC
        ) AS rn
    FROM params d
    JOIN grouped g
      ON 1 = 1
    JOIN qt_performancedata p
      ON p.INNERCODE = g.INNERCODE
     AND (
          (p.TRADINGDAY >= d.observation_date AND p.TRADINGDAY < DATE_ADD(d.observation_date, INTERVAL 1 DAY))
          OR
          (p.TRADINGDAY >= d.future_date AND p.TRADINGDAY < DATE_ADD(d.future_date, INTERVAL 1 DAY))
     )
),
benchmark_ranked AS (
    SELECT
        DATE(q.TRADINGDAY) AS trade_date,
        q.CLOSEPRICE,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(q.TRADINGDAY)
            ORDER BY q.JSID DESC, q.ID DESC
        ) AS rn
    FROM params p
    JOIN target_index ti
      ON 1 = 1
    JOIN qt_indexquote q
      ON q.INNERCODE = ti.INNERCODE
     AND (
          (q.TRADINGDAY >= p.observation_date AND q.TRADINGDAY < DATE_ADD(p.observation_date, INTERVAL 1 DAY))
          OR
          (q.TRADINGDAY >= p.future_date AND q.TRADINGDAY < DATE_ADD(p.future_date, INTERVAL 1 DAY))
     )
),
returns AS (
    SELECT
        g.pe_group,
        p1.adj_close / p0.adj_close - 1
          - (b1.CLOSEPRICE / b0.CLOSEPRICE - 1) AS excess_return
    FROM params d
    JOIN grouped g
      ON 1 = 1
    JOIN performance_ranked p0
      ON p0.INNERCODE = g.INNERCODE
     AND p0.trade_date = d.observation_date
     AND p0.rn = 1
    JOIN performance_ranked p1
      ON p1.INNERCODE = g.INNERCODE
     AND p1.trade_date = d.future_date
     AND p1.rn = 1
    JOIN benchmark_ranked b0
      ON b0.trade_date = d.observation_date
     AND b0.rn = 1
    JOIN benchmark_ranked b1
      ON b1.trade_date = d.future_date
     AND b1.rn = 1
    WHERE p0.adj_close > 0
      AND p1.adj_close > 0
      AND b0.CLOSEPRICE > 0
      AND b1.CLOSEPRICE > 0
),
summary AS (
    SELECT
        AVG(CASE WHEN pe_group = 1 THEN excess_return END) AS low_pe_return,
        AVG(CASE WHEN pe_group = 5 THEN excess_return END) AS high_pe_return
    FROM returns
)
SELECT
    DATE_FORMAT(p.observation_date, '%Y-%m-%d') AS 观察日,
    ROUND(s.low_pe_return * 100, 2) AS 最低PE组平均超额收益,
    ROUND(s.high_pe_return * 100, 2) AS 最高PE组平均超额收益,
    ROUND((s.low_pe_return - s.high_pe_return) * 100, 2) AS 低PE组减高PE组收益差
FROM params p
JOIN summary s
  ON s.low_pe_return IS NOT NULL
 AND s.high_pe_return IS NOT NULL;
