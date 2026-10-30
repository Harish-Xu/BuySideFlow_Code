WITH params AS (
    SELECT DATE('2024-10-28') AS as_of_date
),
latest_trade AS (
    SELECT MAX(t.TRADINGDATE) AS trade_date
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET IN (83, 90)
),
stock_pool AS (
    SELECT
        s.INNERCODE,
        s.SECUCODE
    FROM secumain s
    JOIN latest_trade lt
      ON s.LISTEDDATE < DATE_SUB(lt.trade_date, INTERVAL 50 DAY)
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR IN (1, 2)
      AND NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = s.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND ra.IMPLEMENTDATE <= lt.trade_date
            AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME) < DATE_ADD(lt.trade_date, INTERVAL 1 DAY)
            AND (
                ra.REMOVEDATE IS NULL
                OR ra.REMOVEDATE > lt.trade_date
                OR ra.REMOVEINFOPUBLDATE > lt.trade_date
            )
      )
),
latest_limit_up AS (
    SELECT
        sp.INNERCODE,
        sp.SECUCODE
    FROM stock_pool sp
    CROSS JOIN latest_trade lt
    JOIN qt_dailyquote q
      ON q.INNERCODE = sp.INNERCODE
     AND q.TRADINGDAY = lt.trade_date
     AND q.TURNOVERVOLUME > 0
    JOIN qt_performancedata p
      ON p.INNERCODE = sp.INNERCODE
     AND p.TRADINGDAY = lt.trade_date
     AND p.SURGEDLIMIT = 1
),
recent_days AS (
    SELECT d.TRADINGDATE
    FROM (
        SELECT
            x.TRADINGDATE,
            ROW_NUMBER() OVER (ORDER BY x.TRADINGDATE DESC) AS rn
        FROM (
            SELECT DISTINCT t.TRADINGDATE
            FROM qt_tradingdaynew t
            JOIN latest_trade lt
              ON t.TRADINGDATE <= lt.trade_date
            WHERE t.IFTRADINGDAY = 1
              AND t.SECUMARKET IN (83, 90)
        ) x
    ) d
    WHERE d.rn <= 20
),
streak_base AS (
    SELECT
        lu.INNERCODE,
        lu.SECUCODE,
        rd.TRADINGDATE,
        COALESCE(p.SURGEDLIMIT, 0) AS surged,
        ROW_NUMBER() OVER (
            PARTITION BY lu.INNERCODE
            ORDER BY rd.TRADINGDATE DESC
        ) AS rn_desc
    FROM latest_limit_up lu
    CROSS JOIN recent_days rd
    LEFT JOIN qt_performancedata p
      ON p.INNERCODE = lu.INNERCODE
     AND p.TRADINGDAY = rd.TRADINGDATE
),
streak_calc AS (
    SELECT
        sb.INNERCODE,
        sb.SECUCODE,
        COALESCE(MIN(CASE WHEN sb.surged <> 1 THEN sb.rn_desc END) - 1, MAX(sb.rn_desc)) AS consecutive_limit_up_days
    FROM streak_base sb
    GROUP BY sb.INNERCODE, sb.SECUCODE
)
SELECT
    s.SECUCODE AS `股票代码`,
    s.consecutive_limit_up_days AS `连续涨停天数`
FROM streak_calc s
ORDER BY s.consecutive_limit_up_days DESC, s.SECUCODE
LIMIT 10;
