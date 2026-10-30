WITH params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
etf_list AS (
    SELECT
        sm.INNERCODE,
        sm.SECUCODE,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, sm.SECUCODE) AS etf_name,
        CASE
            WHEN sm.SECUCODE IN ('510300', '159915') THEN 'A股指数'
            WHEN sm.SECUCODE IN ('513100', '513030', '513520') THEN '全球股指'
            WHEN sm.SECUCODE IN ('159980', '159981', '159985') THEN '商品/期货'
        END AS asset_category
    FROM secumain sm
    WHERE sm.SECUCATEGORY = 8
      AND sm.SECUMARKET IN (83, 90)
      AND sm.SECUCODE IN ('510300', '159915', '513100', '513030', '513520', '159980', '159981', '159985')
),
trade_asof AS (
    SELECT MAX(t.TRADINGDATE) AS trade_day
    FROM qt_tradingdaynew t CROSS JOIN params p
    WHERE t.IFTRADINGDAY = 1 AND t.SECUMARKET IN (83, 90)
      AND t.TRADINGDATE <= p.as_of_date
),
market_days AS (
    SELECT trading_day, ROW_NUMBER() OVER (ORDER BY trading_day DESC) AS rn
    FROM (
        SELECT DISTINCT DATE(t.TRADINGDATE) AS trading_day
        FROM qt_tradingdaynew t CROSS JOIN trade_asof a
        WHERE t.IFTRADINGDAY = 1 AND t.SECUMARKET IN (83, 90)
          AND t.TRADINGDATE <= a.trade_day
        ORDER BY trading_day DESC LIMIT 14
    ) d
),
price_series AS (
    SELECT
        el.asset_category,
        el.SECUCODE,
        el.etf_name,
        q.CLOSEPRICE,
        d.rn
    FROM etf_list el CROSS JOIN market_days d
    LEFT JOIN qt_fundsperformancehis q
      ON q.INNERCODE = el.INNERCODE AND q.TRADINGDAY = d.trading_day
),
calc AS (
    SELECT
        asset_category,
        SECUCODE,
        etf_name,
        MAX(CASE WHEN rn = 1 THEN CLOSEPRICE END) AS latest_close,
        MAX(CASE WHEN rn = 14 THEN CLOSEPRICE END) AS close_13d_ago,
        AVG(CASE WHEN rn <= 10 THEN CLOSEPRICE END) AS ma10,
        COUNT(CASE WHEN rn <= 10 AND CLOSEPRICE > 0 THEN 1 END) AS ma_obs_cnt
    FROM price_series
    WHERE rn <= 14
    GROUP BY asset_category, SECUCODE, etf_name
),
ranked AS (
    SELECT
        asset_category,
        SECUCODE,
        etf_name,
        (latest_close / NULLIF(close_13d_ago, 0) - 1) * 100 AS momentum_score,
        ROW_NUMBER() OVER (
            PARTITION BY asset_category
            ORDER BY (latest_close / NULLIF(close_13d_ago, 0) - 1) DESC, SECUCODE
        ) AS rn
    FROM calc
    WHERE ma_obs_cnt = 10
      AND latest_close > 0
      AND close_13d_ago > 0
      AND ma10 IS NOT NULL
      AND latest_close / NULLIF(close_13d_ago, 0) - 1 > 0
      AND latest_close > ma10
)
SELECT
    asset_category AS `类别`,
    SECUCODE AS `ETF代码`,
    etf_name AS `ETF名称`,
    ROUND(momentum_score, 2) AS `动量得分`
FROM ranked
WHERE rn = 1
ORDER BY `类别`;
