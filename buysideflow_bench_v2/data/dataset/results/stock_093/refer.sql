WITH params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
trade_asof AS (
    SELECT MAX(t.TRADINGDATE) AS trade_day
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET IN (83, 90)
),
csi500_index AS (
    SELECT INNERCODE AS index_innercode
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '000905'
),
index_components AS (
    SELECT DISTINCT ic.SECUINNERCODE AS innercode
    FROM lc_indexcomponent ic
    JOIN csi500_index ci
      ON ci.index_innercode = ic.INDEXINNERCODE
    JOIN trade_asof ta
      ON ic.INDATE <= ta.trade_day
    WHERE (ic.OUTDATE IS NULL OR ic.OUTDATE > ta.trade_day)
),
top50_stocks AS (
    SELECT
        v.INNERCODE
    FROM lc_dindicesforvaluation v
    JOIN index_components ic
      ON ic.innercode = v.INNERCODE
    JOIN trade_asof ta
      ON v.TRADINGDAY = ta.trade_day
    WHERE v.NEGOTIABLEMV IS NOT NULL
    ORDER BY v.NEGOTIABLEMV ASC, v.INNERCODE
    LIMIT 50
),
recent_trading_days AS (
    SELECT tradingdate
    FROM (
        SELECT DISTINCT t.TRADINGDATE AS tradingdate
        FROM qt_tradingdaynew t
        JOIN trade_asof ta
          ON t.TRADINGDATE <= ta.trade_day
        WHERE t.IFTRADINGDAY = 1
          AND t.SECUMARKET IN (83, 90)
        ORDER BY t.TRADINGDATE DESC
        LIMIT 25
    ) d
),
price_data AS (
    SELECT
        q.INNERCODE,
        q.TRADINGDAY,
        q.CLOSEPRICE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name
    FROM qt_dailyquote q
    JOIN top50_stocks t
      ON t.INNERCODE = q.INNERCODE
    JOIN secumain s
      ON s.INNERCODE = q.INNERCODE
    JOIN recent_trading_days d
      ON d.tradingdate = q.TRADINGDAY
    WHERE q.CLOSEPRICE IS NOT NULL
      AND q.CLOSEPRICE > 0
),
complete_price_data AS (
    SELECT p.*
    FROM price_data p
    JOIN (
        SELECT INNERCODE
        FROM price_data
        GROUP BY INNERCODE
        HAVING COUNT(DISTINCT TRADINGDAY) = 25
    ) c
      ON c.INNERCODE = p.INNERCODE
),
ordered_prices AS (
    SELECT
        *,
        AVG(CLOSEPRICE) OVER (
            PARTITION BY INNERCODE
            ORDER BY TRADINGDAY
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) AS ma20,
        STDDEV_POP(CLOSEPRICE) OVER (
            PARTITION BY INNERCODE
            ORDER BY TRADINGDAY
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) AS std20,
        MIN(CLOSEPRICE) OVER (
            PARTITION BY INNERCODE
            ORDER BY TRADINGDAY
            ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
        ) AS prev10_min
    FROM complete_price_data
),
bands AS (
    SELECT
        *,
        ma20 + 2 * std20 AS upper_band,
        ma20 - 2 * std20 AS lower_band,
        LAG(CLOSEPRICE) OVER (PARTITION BY INNERCODE ORDER BY TRADINGDAY) AS prev_close,
        LAG(ma20 - 2 * std20) OVER (PARTITION BY INNERCODE ORDER BY TRADINGDAY) AS prev_lower_band
    FROM ordered_prices
),
signals AS (
    SELECT
        b.INNERCODE,
        b.stock_name,
        b.TRADINGDAY,
        b.CLOSEPRICE,
        b.upper_band,
        b.lower_band,
        CASE
            WHEN b.prev_close < b.prev_lower_band
             AND b.CLOSEPRICE > b.prev_close
             AND b.CLOSEPRICE > b.prev10_min
            THEN 'BUY'
            ELSE 'HOLD'
        END AS buy_condition,
        ROW_NUMBER() OVER (PARTITION BY b.INNERCODE ORDER BY b.TRADINGDAY DESC) AS rn
    FROM bands b
    WHERE b.upper_band IS NOT NULL
      AND b.lower_band IS NOT NULL
      AND b.prev_lower_band IS NOT NULL
      AND b.prev10_min IS NOT NULL
)
SELECT
    s.SECUCODE AS `股票代码`,
    sig.stock_name AS `股票简称`,
    ROUND(sig.CLOSEPRICE, 4) AS `收盘价`,
    ROUND(sig.upper_band, 4) AS `布林带上轨`,
    ROUND(sig.lower_band, 4) AS `布林带下轨`,
    sig.buy_condition AS `买入条件`
FROM signals sig
JOIN secumain s
  ON s.INNERCODE = sig.INNERCODE
WHERE sig.rn = 1
  AND sig.buy_condition = 'BUY'
ORDER BY sig.CLOSEPRICE DESC, s.SECUCODE;
