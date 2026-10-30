WITH
params AS (SELECT DATE('2025-08-24') AS as_of_date),
industry_asof AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC,
                         e.INSERTTIME DESC,
                         e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
          AND e.FIRSTINDUSTRYNAME IS NOT NULL
    ) t
    WHERE rn = 1
),
stock_pool AS (
    SELECT
        INNERCODE,
        COMPANYCODE,
        SECUCODE,
        stock_name
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUCODE,
            COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY CASE WHEN s.LISTEDSECTOR = 1 THEN 0 ELSE 1 END,
                         s.SECUCODE
            ) AS rn
        FROM secumain s
        JOIN industry_asof ia
          ON ia.COMPANYCODE = s.COMPANYCODE
        JOIN params p
          ON s.LISTEDDATE <= DATE_SUB(p.as_of_date, INTERVAL 1 YEAR)
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 6)
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              WHERE ra.INNERCODE = s.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
                AND DATE(ra.IMPLEMENTDATE) <= p.as_of_date
                AND (
                    ra.REMOVEDATE IS NULL
                    OR DATE(ra.REMOVEDATE) > p.as_of_date
                    OR DATE(COALESCE(ra.REMOVEINFOPUBLDATE, ra.REMOVEDATE)) > p.as_of_date
                )
          )
          AND ia.FIRSTINDUSTRYNAME NOT IN ('银行', '非银金融', '房地产')
    ) t
    WHERE rn = 1
),
latest_trade_day AS (
    SELECT MAX(t.TRADINGDATE) AS trading_day
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET = 83
),
lookback_days AS (
    SELECT
        t.trading_day,
        ROW_NUMBER() OVER (ORDER BY t.trading_day DESC) AS rn_desc
    FROM (
        SELECT DISTINCT q.TRADINGDATE AS trading_day
        FROM qt_tradingdaynew q
        JOIN latest_trade_day ltd
          ON q.TRADINGDATE <= ltd.trading_day
        WHERE q.IFTRADINGDAY = 1
          AND q.SECUMARKET = 83
    ) t
),
ret_window AS (
    SELECT
        e.trading_day AS end_td,
        s.trading_day AS start_td
    FROM (SELECT trading_day FROM lookback_days WHERE rn_desc = 1) e
    CROSS JOIN (SELECT trading_day FROM lookback_days WHERE rn_desc = 21) s
),
ret20 AS (
    SELECT
        sp.INNERCODE,
        MAX(CASE WHEN q.TRADINGDAY = rw.end_td THEN q.BACKWARDPRICE END) AS px_end,
        MAX(CASE WHEN q.TRADINGDAY = rw.start_td THEN q.BACKWARDPRICE END) AS px_start
    FROM stock_pool sp
    JOIN ret_window rw
      ON 1 = 1
    JOIN qt_performancedata q
      ON q.INNERCODE = sp.INNERCODE
     AND q.TRADINGDAY IN (rw.start_td, rw.end_td)
    WHERE q.BACKWARDPRICE IS NOT NULL
      AND q.BACKWARDPRICE > 0
    GROUP BY sp.INNERCODE
    HAVING px_end IS NOT NULL
       AND px_start IS NOT NULL
),
ret20_scored AS (
    SELECT
        INNERCODE,
        px_end / px_start - 1 AS ret_20d,
        PERCENT_RANK() OVER (ORDER BY (px_end / px_start - 1) ASC) AS ret_pct_rank
    FROM ret20
),
drawdown_pool AS (
    SELECT
        r.INNERCODE,
        sp.COMPANYCODE,
        sp.SECUCODE,
        sp.stock_name,
        r.ret_20d
    FROM ret20_scored r
    JOIN stock_pool sp
      ON sp.INNERCODE = r.INNERCODE
    WHERE r.ret_pct_rank <= 0.10
      AND r.ret_20d <= -0.05
),
fin_dedup AS (
    SELECT
        f.COMPANYCODE,
        DATE(f.ENDDATE) AS end_date,
        f.INFOPUBLDATE,
        f.MARK,
        f.WROE,
        f.NETPROFITRATIO,
        f.CURRENTRATIO,
        f.DEBTASSETSRATIO,
        ROW_NUMBER() OVER (
            PARTITION BY f.COMPANYCODE, DATE(f.ENDDATE)
            ORDER BY CASE f.MARK
                         WHEN 1 THEN 0
                         WHEN 8 THEN 1
                         WHEN 7 THEN 2
                         WHEN 6 THEN 3
                         WHEN 2 THEN 4
                         ELSE 9
                     END,
                     f.INFOPUBLDATE DESC,
                     f.JSID DESC
        ) AS rn
    FROM fin_derivative f
    JOIN drawdown_pool dp
      ON dp.COMPANYCODE = f.COMPANYCODE
    JOIN params p
      ON f.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE MONTH(f.ENDDATE) IN (3, 6, 9, 12)
      AND f.WROE IS NOT NULL
      AND f.NETPROFITRATIO IS NOT NULL
      AND f.CURRENTRATIO IS NOT NULL
      AND f.DEBTASSETSRATIO IS NOT NULL
      AND f.DEBTASSETSRATIO > 0
),
fin_q AS (
    SELECT
        COMPANYCODE,
        end_date,
        WROE,
        NETPROFITRATIO,
        CURRENTRATIO,
        DEBTASSETSRATIO
    FROM fin_dedup
    WHERE rn = 1
),
latest_common_enddate AS (
    SELECT f.end_date AS latest_end_date
    FROM fin_q f
    GROUP BY f.end_date
    HAVING COUNT(DISTINCT f.COMPANYCODE) = (SELECT COUNT(*) FROM drawdown_pool)
    ORDER BY f.end_date DESC
    LIMIT 1
),
fin_latest AS (
    SELECT
        f.COMPANYCODE,
        f.WROE,
        f.NETPROFITRATIO,
        f.CURRENTRATIO,
        f.DEBTASSETSRATIO
    FROM fin_q f
    JOIN latest_common_enddate d
      ON f.end_date = d.latest_end_date
),
fund_panel AS (
    SELECT
        dp.SECUCODE,
        dp.stock_name,
        dp.ret_20d,
        fl.WROE * fl.NETPROFITRATIO AS profit_quality,
        fl.CURRENTRATIO / NULLIF(fl.DEBTASSETSRATIO, 0) AS financial_health
    FROM drawdown_pool dp
    JOIN fin_latest fl
      ON fl.COMPANYCODE = dp.COMPANYCODE
),
metric_ranked AS (
    SELECT
        *,
        1 - PERCENT_RANK() OVER (ORDER BY profit_quality DESC) AS profit_quality_score,
        1 - PERCENT_RANK() OVER (ORDER BY financial_health DESC) AS financial_health_score
    FROM fund_panel
),
ranked AS (
    SELECT
        *,
        (profit_quality_score + financial_health_score) / 2.0 AS composite_score
    FROM metric_ranked
)
SELECT
    SECUCODE AS 代码,
    stock_name AS 名称,
    ROUND(-ret_20d * 100, 2) AS 近20日跌幅,
    ROUND(profit_quality, 4) AS 盈利质量,
    ROUND(financial_health, 4) AS 财务健康度,
    ROUND(composite_score, 4) AS 综合得分
FROM ranked
ORDER BY composite_score DESC, ret_20d ASC, 代码 ASC
LIMIT 30;
