WITH RECURSIVE
params AS (
    SELECT TIMESTAMP('2025-10-16 23:59:59') AS as_of_dt
),
latest_trade AS (
    SELECT MAX(t.TRADINGDATE) AS trade_day
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= DATE(p.as_of_dt)
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET = 83
),
trade_days AS (
    SELECT
        d.TRADINGDATE AS trading_day,
        ROW_NUMBER() OVER (ORDER BY d.TRADINGDATE DESC) AS rn_desc
    FROM (
        SELECT DISTINCT t.TRADINGDATE
        FROM qt_tradingdaynew t
        JOIN latest_trade lt
          ON t.TRADINGDATE <= lt.trade_day
        WHERE t.IFTRADINGDAY = 1
          AND t.SECUMARKET = 83
    ) d
),
listing_cutoff AS (
    SELECT trading_day AS cutoff_day
    FROM trade_days
    WHERE rn_desc = 50
),
last_5_days AS (
    SELECT trading_day
    FROM trade_days
    WHERE rn_desc <= 5
),
stock_base AS (
    SELECT
        s.INNERCODE,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name
    FROM secumain s
    JOIN listing_cutoff lc
      ON DATE(s.LISTEDDATE) <= lc.cutoff_day
    JOIN latest_trade lt
      ON 1 = 1
    JOIN params p
      ON 1 = 1
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR = 1
      AND NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = s.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= p.as_of_dt
            AND DATE(ra.IMPLEMENTDATE) <= lt.trade_day
            AND (
                ra.REMOVEDATE IS NULL
                OR DATE(ra.REMOVEDATE) > lt.trade_day
                OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) > p.as_of_dt
            )
      )
),
latest_limit_pool AS (
    SELECT sb.INNERCODE
    FROM stock_base sb
    JOIN qt_performancedata pd
      ON pd.INNERCODE = sb.INNERCODE
    JOIN latest_trade lt
      ON pd.TRADINGDAY = lt.trade_day
    WHERE pd.SURGEDLIMIT = 1
),
current_run AS (
    SELECT
        lp.INNERCODE,
        1 AS rn_desc,
        1 AS streak_len
    FROM latest_limit_pool lp

    UNION ALL

    SELECT
        cr.INNERCODE,
        td.rn_desc,
        cr.streak_len + 1
    FROM current_run cr
    JOIN trade_days td
      ON td.rn_desc = cr.rn_desc + 1
    JOIN qt_performancedata pd
      ON pd.INNERCODE = cr.INNERCODE
     AND pd.TRADINGDAY = td.trading_day
     AND pd.SURGEDLIMIT = 1
),
current_streak AS (
    SELECT
        INNERCODE,
        MAX(streak_len) AS consecutive_limit_up
    FROM current_run
    GROUP BY INNERCODE
    HAVING MAX(streak_len) >= 2
),
leaders AS (
    SELECT cs.*
    FROM current_streak cs
    JOIN (
        SELECT MAX(consecutive_limit_up) AS max_streak
        FROM current_streak
    ) m
      ON cs.consecutive_limit_up = m.max_streak
),
turnover_5d AS (
    SELECT
        l.INNERCODE,
        AVG(COALESCE(qp.TURNOVERRATE, 0)) AS avg_turnover_rate_5d
    FROM leaders l
    CROSS JOIN last_5_days d
    LEFT JOIN qt_performance qp
      ON qp.INNERCODE = l.INNERCODE
     AND qp.TRADINGDAY = d.trading_day
    GROUP BY l.INNERCODE
)
SELECT
    sb.SECUCODE AS `股票代码`,
    sb.stock_name AS `股票名称`,
    l.consecutive_limit_up AS `连板数`,
    ROUND(t.avg_turnover_rate_5d, 2) AS `近5日日均换手率`
FROM leaders l
JOIN stock_base sb
  ON sb.INNERCODE = l.INNERCODE
JOIN turnover_5d t
  ON t.INNERCODE = l.INNERCODE
ORDER BY t.avg_turnover_rate_5d DESC,
         sb.SECUCODE ASC
LIMIT 10;
