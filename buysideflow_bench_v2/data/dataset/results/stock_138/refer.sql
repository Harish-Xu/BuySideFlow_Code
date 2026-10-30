WITH trade_window AS (
    SELECT td.TRADINGDATE
    FROM qt_tradingdaynew td
    WHERE td.IFTRADINGDAY = 1
      AND td.SECUMARKET = 83
      AND td.TRADINGDATE BETWEEN '2025-12-01' AND '2025-12-31'
),
daily_quotes AS (
    SELECT q.TRADINGDAY, s.SECUCODE, q.TURNOVERVALUE
    FROM qt_dailyquote q
    JOIN secumain s
      ON s.INNERCODE = q.INNERCODE
    JOIN trade_window tw
      ON tw.TRADINGDATE = q.TRADINGDAY
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR IN (1, 2)
),
daily_turnover AS (
    SELECT
        q.TRADINGDAY,
        q.SECUCODE,
        q.TURNOVERVALUE,
        ROW_NUMBER() OVER (
            PARTITION BY q.TRADINGDAY
            ORDER BY q.TURNOVERVALUE DESC, q.SECUCODE
        ) AS rn_desc,
        COUNT(*) OVER (PARTITION BY q.TRADINGDAY) AS n_total
    FROM daily_quotes q
    WHERE q.TURNOVERVALUE > 0
),
daily_crowding AS (
    SELECT
        TRADINGDAY,
        SUM(CASE WHEN rn_desc <= CEIL(0.05 * n_total) THEN TURNOVERVALUE ELSE 0 END) AS top_turnover,
        SUM(TURNOVERVALUE) AS total_turnover
    FROM daily_turnover
    GROUP BY TRADINGDAY
)
SELECT
    TRADINGDAY AS `交易日`,
    ROUND(top_turnover / NULLIF(total_turnover, 0) * 100, 2) AS `拥挤度(%)`,
    CASE
        WHEN top_turnover / NULLIF(total_turnover, 0) > 0.55 THEN '高拥挤'
        WHEN top_turnover / NULLIF(total_turnover, 0) < 0.33 THEN '低拥挤'
        ELSE '中性'
    END AS `拥挤度信号`
FROM daily_crowding
WHERE total_turnover > 0
ORDER BY `交易日` DESC;
