WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
trade_days AS (
    SELECT DISTINCT DATE(t.TRADINGDATE) AS TRADINGDATE
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET = 83
),
anchor AS (
    SELECT MAX(TRADINGDATE) AS anchor_date
    FROM trade_days
),
trade_refs AS (
    SELECT
        a.anchor_date,
        MAX(CASE
            WHEN d.TRADINGDATE <= DATE_SUB(a.anchor_date, INTERVAL 3 MONTH)
            THEN d.TRADINGDATE
        END) AS start_3m,
        MAX(CASE
            WHEN d.TRADINGDATE <= DATE_SUB(a.anchor_date, INTERVAL 6 MONTH)
            THEN d.TRADINGDATE
        END) AS start_6m
    FROM anchor a
    CROSS JOIN trade_days d
    GROUP BY a.anchor_date
),
industry_indices AS (
    SELECT
        sm.INNERCODE,
        sm.SECUCODE AS industry_code,
        REPLACE(COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME), '申万', '') AS industry_name
    FROM lc_indexbasicinfo bi
    JOIN secumain sm
      ON sm.INNERCODE = bi.INDEXCODE
    JOIN params p
      ON bi.PUBDATE IS NULL OR bi.PUBDATE <= p.as_of_date
    WHERE bi.INDUSTRYSTANDARD = 38
      AND bi.PUBINDEXTYPE = 121101
      AND sm.SECUCATEGORY = 4
),
endpoint_prices AS (
    SELECT
        ii.industry_code,
        ii.industry_name,
        MAX(CASE WHEN q.TRADINGDAY = tr.anchor_date THEN q.CLOSEPRICE END) AS current_close,
        MAX(CASE WHEN q.TRADINGDAY = tr.start_3m THEN q.CLOSEPRICE END) AS close_3m,
        MAX(CASE WHEN q.TRADINGDAY = tr.start_6m THEN q.CLOSEPRICE END) AS close_6m
    FROM industry_indices ii
    CROSS JOIN trade_refs tr
    JOIN qt_indexquote q
      ON q.INNERCODE = ii.INNERCODE
     AND q.TRADINGDAY IN (tr.anchor_date, tr.start_3m, tr.start_6m)
    GROUP BY ii.industry_code, ii.industry_name
    HAVING current_close > 0
       AND close_3m > 0
       AND close_6m > 0
),
industry_returns AS (
    SELECT
        industry_code,
        industry_name,
        current_close / close_3m - 1 AS return_3m,
        current_close / close_6m - 1 AS return_6m
    FROM endpoint_prices
),
market_average AS (
    SELECT
        AVG(return_3m) AS avg_return_3m,
        AVG(return_6m) AS avg_return_6m
    FROM industry_returns
),
ranked AS (
    SELECT
        ir.industry_code,
        ir.industry_name,
        ir.return_3m - ma.avg_return_3m AS excess_return_3m,
        ir.return_6m - ma.avg_return_6m AS excess_return_6m,
        ROW_NUMBER() OVER (
            ORDER BY
                (ir.return_3m - ma.avg_return_3m)
                + (ir.return_6m - ma.avg_return_6m) DESC,
                ir.industry_code
        ) AS rn
    FROM industry_returns ir
    CROSS JOIN market_average ma
)
SELECT
    industry_code AS `行业指数代码`,
    industry_name AS `行业名称`,
    ROUND(excess_return_3m * 100, 2) AS `过去3个月超额收益`,
    ROUND(excess_return_6m * 100, 2) AS `过去6个月超额收益`
FROM ranked
WHERE rn <= 5
ORDER BY rn;
