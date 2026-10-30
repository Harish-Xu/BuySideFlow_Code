-- Three indices, five trading days versus the preceding non-overlapping twenty.
-- XGRQ is a revision timestamp, not a disclosure date; use the configured snapshot.
WITH requested AS (
    SELECT '000300' AS index_code, '沪深300' AS index_name, 1 AS ord
    UNION ALL SELECT '000905', '中证500', 2
    UNION ALL SELECT '000852', '中证1000', 3
), index_ids AS (
    SELECT r.index_code, r.index_name, r.ord,
           CASE WHEN COUNT(s.INNERCODE) = 1 THEN MIN(s.INNERCODE) END AS INNERCODE
    FROM requested r
    LEFT JOIN secumain s ON s.SECUCODE = r.index_code
      AND s.SECUCATEGORY = 4 AND s.SECUMARKET = 83
    GROUP BY r.index_code, r.index_name, r.ord
), trading_dates AS (
    SELECT DISTINCT DATE(TRADINGDATE) AS trading_day
    FROM qt_tradingdaynew
    WHERE SECUMARKET IN (83, 90) AND IFTRADINGDAY = 1
      AND TRADINGDATE >= '2025-01-01' AND TRADINGDATE < '2026-01-01'
), ranked_dates AS (
    SELECT trading_day, ROW_NUMBER() OVER (ORDER BY trading_day DESC) AS rn
    FROM trading_dates
), trading_window AS (
    SELECT trading_day, rn FROM ranked_dates WHERE rn <= 25
), quote_ranked AS (
    SELECT q.INNERCODE, DATE(q.TRADINGDAY) AS trading_day,
           q.CLOSEPRICE, q.TURNOVERVALUE,
           ROW_NUMBER() OVER (
               PARTITION BY q.INNERCODE, DATE(q.TRADINGDAY)
               ORDER BY q.XGRQ DESC, q.ID DESC
           ) AS version_rank
    FROM qt_indexquote q
    JOIN index_ids i ON i.INNERCODE = q.INNERCODE
    WHERE q.TRADINGDAY >= (SELECT MIN(trading_day) FROM trading_window)
      AND q.TRADINGDAY < '2026-01-01'
), aggregates AS (
    SELECT i.index_name, i.ord,
           CASE WHEN COUNT(CASE WHEN w.rn <= 5 AND q.TURNOVERVALUE >= 0 THEN 1 END) = 5
                THEN AVG(CASE WHEN w.rn <= 5 THEN q.TURNOVERVALUE END) END AS avg_5,
           CASE WHEN COUNT(CASE WHEN w.rn BETWEEN 6 AND 25 AND q.TURNOVERVALUE >= 0 THEN 1 END) = 20
                THEN AVG(CASE WHEN w.rn BETWEEN 6 AND 25 THEN q.TURNOVERVALUE END) END AS avg_20,
           MAX(CASE WHEN w.rn = 1 THEN q.CLOSEPRICE END) AS close_end,
           MAX(CASE WHEN w.rn = 6 THEN q.CLOSEPRICE END) AS close_before
    FROM index_ids i
    LEFT JOIN trading_window w ON 1 = 1
    LEFT JOIN quote_ranked q ON q.INNERCODE = i.INNERCODE
      AND q.trading_day = w.trading_day AND q.version_rank = 1
    GROUP BY i.index_name, i.ord
), calculated AS (
    SELECT *,
           CASE WHEN avg_5 IS NOT NULL AND avg_20 > 0
                THEN avg_5 / avg_20 - 1 END AS amount_change,
           CASE WHEN close_end > 0 AND close_before > 0
                THEN close_end / close_before - 1 END AS return_5
    FROM aggregates
)
SELECT index_name AS `指数简称`,
       ROUND(avg_5 / 100000000.0, 2) AS `近5日日均成交额`,
       ROUND(avg_20 / 100000000.0, 2) AS `此前20日日均成交额`,
       ROUND(amount_change * 100, 2) AS `成交额变化率`,
       ROUND(return_5 * 100, 2) AS `近5日涨跌幅`
FROM calculated
ORDER BY amount_change IS NULL, amount_change DESC, ord;
