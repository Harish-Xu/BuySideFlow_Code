WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
trade_days AS (
    SELECT
        TRADINGDATE,
        ROW_NUMBER() OVER (ORDER BY TRADINGDATE DESC) AS rn_desc
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET = 83
),
trade_refs AS (
    SELECT
        MAX(CASE WHEN rn_desc = 1 THEN TRADINGDATE END) AS latest_trade_day
    FROM trade_days
),
window_20d AS (
    SELECT TRADINGDATE
    FROM trade_days
    WHERE rn_desc <= 20
),
stock_base AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        s.LISTEDSECTOR
    FROM (
        SELECT
            s.*,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.INNERCODE
            ) AS rn
        FROM secumain s
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90, 18)
          AND s.LISTEDSECTOR IN (1, 2, 6, 7, 8)
    ) s
    WHERE s.rn = 1
),
industry_asof AS (
    SELECT
        COMPANYCODE,
        FIRSTINDUSTRYCODE,
        FIRSTINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC,
                         e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR <> 7
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
        UNION ALL
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
            ) AS rn
        FROM lc_stibexgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR = 7
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND e.EFFECTIVEDATE <= p.as_of_date
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    ) t
    WHERE rn = 1
),
latest_ma20 AS (
    SELECT
        sb.INNERCODE,
        MAX(CASE WHEN q.TRADINGDAY = tr.latest_trade_day THEN q.CLOSEPRICE END) AS close_price,
        AVG(q.CLOSEPRICE) AS ma20
    FROM stock_base sb
    JOIN qt_dailyquote q
      ON q.INNERCODE = sb.INNERCODE
    JOIN window_20d w
      ON w.TRADINGDATE = q.TRADINGDAY
    JOIN trade_refs tr
    GROUP BY sb.INNERCODE
    HAVING COUNT(*) = 20
),
joined AS (
    SELECT
        ia.FIRSTINDUSTRYCODE,
        ia.FIRSTINDUSTRYNAME,
        lm.close_price,
        lm.ma20
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    JOIN latest_ma20 lm
      ON lm.INNERCODE = sb.INNERCODE
),
market_stats AS (
    SELECT
        ROUND(SUM(CASE WHEN close_price > ma20 THEN 1 ELSE 0 END) / COUNT(*) * 100, 4) AS market_above_ma20_ratio
    FROM joined
)
SELECT
    ms.market_above_ma20_ratio AS `全市场收盘价高于MA20的股票占比`,
    j.FIRSTINDUSTRYCODE AS `行业代码`,
    j.FIRSTINDUSTRYNAME AS `行业名称`,
    ROUND(SUM(CASE WHEN j.close_price > j.ma20 THEN 1 ELSE 0 END) / COUNT(*) * 100, 4) AS `行业收盘价高于MA20的股票占比`
FROM joined j
CROSS JOIN market_stats ms
GROUP BY ms.market_above_ma20_ratio, j.FIRSTINDUSTRYCODE, j.FIRSTINDUSTRYNAME
ORDER BY j.FIRSTINDUSTRYCODE;
