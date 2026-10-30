WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
trade_day AS (
    SELECT MAX(t.TRADINGDATE) AS latest_trade_day
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET IN (83, 90)
),
stock_base AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        s.SECUCODE,
        COALESCE(s.CHINAMEABBR, s.SECUABBR, s.CHINAME, s.SECUCODE) AS stock_name
    FROM secumain s
    CROSS JOIN trade_day td
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR = 1
      AND s.LISTEDSTATE = 1
      AND NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = s.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND ra.IMPLEMENTDATE <= td.latest_trade_day
            AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) < DATE_ADD(td.latest_trade_day, INTERVAL 1 DAY)
            AND (
                ra.REMOVEDATE IS NULL
                OR ra.REMOVEDATE > td.latest_trade_day
                OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) >= DATE_ADD(td.latest_trade_day, INTERVAL 1 DAY)
            )
      )
),
recent_trade_days AS (
    SELECT TRADINGDATE, rn_desc
    FROM (
        SELECT
            d.TRADINGDATE,
            ROW_NUMBER() OVER (ORDER BY d.TRADINGDATE DESC) AS rn_desc
        FROM (
            SELECT DISTINCT t.TRADINGDATE
            FROM qt_tradingdaynew t
            JOIN trade_day td
              ON t.TRADINGDATE <= td.latest_trade_day
            WHERE t.IFTRADINGDAY = 1
              AND t.SECUMARKET = 83
        ) d
    ) t
    WHERE rn_desc <= 4
),
current_limit_up AS (
    SELECT
        sb.INNERCODE,
        sb.COMPANYCODE,
        sb.SECUCODE,
        sb.stock_name
    FROM stock_base sb
    JOIN qt_performancedata pd
      ON pd.INNERCODE = sb.INNERCODE
    JOIN trade_day td
      ON pd.TRADINGDAY = td.latest_trade_day
    WHERE pd.SURGEDLIMIT = 1
),
streak_scan AS (
    SELECT
        clu.INNERCODE,
        d.rn_desc,
        COALESCE(pd.SURGEDLIMIT, 0) AS surged
    FROM current_limit_up clu
    CROSS JOIN recent_trade_days d
    LEFT JOIN qt_performancedata pd
      ON pd.INNERCODE = clu.INNERCODE
     AND pd.TRADINGDAY = d.TRADINGDATE
),
streaks AS (
    SELECT
        INNERCODE,
        COALESCE(
            MIN(CASE WHEN surged <> 1 THEN rn_desc END) - 1,
            MAX(rn_desc)
        ) AS consecutive_boards
    FROM streak_scan
    GROUP BY INNERCODE
),
limit_up_pool AS (
    SELECT
        clu.*,
        CAST(s.consecutive_boards AS UNSIGNED) AS consecutive_boards
    FROM current_limit_up clu
    JOIN streaks s
      ON s.INNERCODE = clu.INNERCODE
),
latest_val AS (
    SELECT INNERCODE, NEGOTIABLEMV
    FROM (
        SELECT
            lp.INNERCODE,
            v.NEGOTIABLEMV,
            ROW_NUMBER() OVER (
                PARTITION BY lp.INNERCODE
                ORDER BY v.JSID DESC, v.ID DESC
            ) AS rn
        FROM limit_up_pool lp
        JOIN lc_dindicesforvaluation v
          ON v.INNERCODE = lp.INNERCODE
        JOIN trade_day td
          ON v.TRADINGDAY = td.latest_trade_day
        WHERE v.NEGOTIABLEMV < 10000000000
          AND v.NEGOTIABLEMV IS NOT NULL
    ) t
    WHERE rn = 1
),
latest_quote AS (
    SELECT INNERCODE, TURNOVERVALUE
    FROM (
        SELECT
            lp.INNERCODE,
            q.TURNOVERVALUE,
            ROW_NUMBER() OVER (
                PARTITION BY lp.INNERCODE
                ORDER BY q.JSID DESC, q.ID DESC
            ) AS rn
        FROM limit_up_pool lp
        JOIN qt_dailyquote q
          ON q.INNERCODE = lp.INNERCODE
        JOIN trade_day td
          ON q.TRADINGDAY = td.latest_trade_day
        WHERE q.TURNOVERVALUE IS NOT NULL
    ) x
    WHERE rn = 1
),
eligible AS (
    SELECT
        CASE
            WHEN lp.consecutive_boards = 1 THEN '首板'
            WHEN lp.consecutive_boards = 2 THEN '二板'
            ELSE '三板及以上'
        END AS board_group,
        lv.NEGOTIABLEMV,
        lq.TURNOVERVALUE / lv.NEGOTIABLEMV * 100 AS turnover_rate
    FROM limit_up_pool lp
    JOIN latest_val lv
      ON lv.INNERCODE = lp.INNERCODE
    JOIN latest_quote lq
      ON lq.INNERCODE = lp.INNERCODE
),
board_groups AS (
    SELECT 1 AS sort_order, '首板' AS board_group
    UNION ALL
    SELECT 2, '二板'
    UNION ALL
    SELECT 3, '三板及以上'
),
summary AS (
    SELECT
        board_group,
        COUNT(*) AS stock_count,
        AVG(NEGOTIABLEMV) / 100000000 AS avg_negotiable_mv_100m,
        AVG(turnover_rate) AS avg_turnover_rate
    FROM eligible
    GROUP BY board_group
)
SELECT
    bg.board_group AS `连板分组`,
    COALESCE(s.stock_count, 0) AS `股票数`,
    ROUND(s.avg_negotiable_mv_100m, 2) AS `平均流通市值`,
    ROUND(s.avg_turnover_rate, 2) AS `平均换手率`
FROM board_groups bg
LEFT JOIN summary s
  ON s.board_group = bg.board_group
ORDER BY bg.sort_order;
