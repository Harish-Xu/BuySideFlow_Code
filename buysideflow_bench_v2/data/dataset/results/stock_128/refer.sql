WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
latest_trade AS (
    SELECT MAX(TRADINGDATE) AS trading_day
    FROM qt_tradingdaynew
    WHERE IFTRADINGDAY = 1
      AND SECUMARKET = 83
      AND TRADINGDATE <= (SELECT as_of_date FROM params)
),
trade_days AS (
    SELECT TRADINGDATE
    FROM (
        SELECT
            td.TRADINGDATE,
            ROW_NUMBER() OVER (ORDER BY td.TRADINGDATE DESC) AS rn_desc
        FROM qt_tradingdaynew td
        JOIN latest_trade lt
          ON td.TRADINGDATE <= lt.trading_day
        WHERE td.IFTRADINGDAY = 1
          AND td.SECUMARKET = 83
    ) t
    WHERE rn_desc <= 252
),
csi800 AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '000906'
    ORDER BY INNERCODE
    LIMIT 1
),
industry_info AS (
    SELECT
        libi.INDEXCODE AS INNERCODE,
        sm.SECUCODE AS industry_code,
        sm.SECUABBR AS industry_name
    FROM lc_indexbasicinfo libi
    JOIN secumain sm
      ON sm.INNERCODE = libi.INDEXCODE
    WHERE libi.PUBINDEXTYPE = 121101
      AND libi.INDUSTRYSTANDARD = 38
),
industry_returns AS (
    SELECT
        ii.industry_code,
        ii.industry_name,
        q.TRADINGDAY,
        q.CHANGEPCT / 100.0 AS industry_return
    FROM industry_info ii
    JOIN qt_indexquote q
      ON q.INNERCODE = ii.INNERCODE
    JOIN trade_days td
      ON td.TRADINGDATE = q.TRADINGDAY
    WHERE q.CHANGEPCT IS NOT NULL
),
benchmark_returns AS (
    SELECT
        q.TRADINGDAY,
        q.CHANGEPCT / 100.0 AS benchmark_return
    FROM qt_indexquote q
    JOIN csi800 c
      ON c.INNERCODE = q.INNERCODE
    JOIN trade_days td
      ON td.TRADINGDATE = q.TRADINGDAY
    WHERE q.CHANGEPCT IS NOT NULL
),
excess_returns AS (
    SELECT
        ir.industry_code,
        ir.industry_name,
        ir.TRADINGDAY,
        ir.industry_return,
        ir.industry_return - br.benchmark_return AS excess_return
    FROM industry_returns ir
    JOIN benchmark_returns br
      ON br.TRADINGDAY = ir.TRADINGDAY
),
industry_stats AS (
    SELECT
        industry_code,
        industry_name,
        EXP(SUM(LN(1 + industry_return)) * 250.0 / COUNT(*)) - 1 AS annualized_return,
        AVG(excess_return) AS avg_excess_return,
        AVG(excess_return) / NULLIF(STDDEV_POP(excess_return), 0) AS information_ratio,
        COUNT(*) AS obs_cnt
    FROM excess_returns
    GROUP BY industry_code, industry_name
)
SELECT
    (SELECT trading_day FROM latest_trade) AS `数据截止日期`,
    industry_code AS `行业代码`,
    industry_name AS `行业名称`,
    ROUND(annualized_return * 100, 4) AS `行业年化收益率(%)`,
    ROUND(avg_excess_return * 100, 4) AS `相对中证800平均超额收益(%)`,
    ROUND(information_ratio, 4) AS `信息比率IR`
FROM industry_stats
WHERE obs_cnt = 252
ORDER BY `信息比率IR` DESC, `行业代码`;
