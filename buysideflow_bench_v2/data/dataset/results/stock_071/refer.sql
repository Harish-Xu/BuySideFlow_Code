WITH
params AS (
    SELECT TIMESTAMP('2025-06-06 23:59:59') AS as_of_dt
),
trade_asof AS (
    SELECT MAX(t.TRADINGDATE) AS trade_date
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_dt
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET = 90
),
gem_stocks AS (
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
                ORDER BY s.INNERCODE
            ) AS rn
        FROM secumain s
        JOIN trade_asof ta
          ON s.LISTEDDATE <= DATE_SUB(ta.trade_date, INTERVAL 2 YEAR)
        JOIN params p
          ON 1 = 1
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET = 90
          AND s.LISTEDSECTOR = 6
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              WHERE ra.INNERCODE = s.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND DATE(ra.IMPLEMENTDATE) <= DATE(p.as_of_dt)
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= p.as_of_dt
                AND (
                    ra.REMOVEDATE IS NULL
                    OR DATE(ra.REMOVEDATE) > DATE(p.as_of_dt)
                    OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) > p.as_of_dt
                )
          )
    ) t
    WHERE rn = 1
),
window_dates AS (
    SELECT trade_date, rn_desc
    FROM (
        SELECT
            DATE(t.TRADINGDATE) AS trade_date,
            ROW_NUMBER() OVER (ORDER BY t.TRADINGDATE DESC) AS rn_desc
        FROM qt_tradingdaynew t
        JOIN trade_asof ta
          ON t.TRADINGDATE <= ta.trade_date
        WHERE t.IFTRADINGDAY = 1
          AND t.SECUMARKET = 90
    ) d
    WHERE rn_desc <= 120
),
quote_versions AS (
    SELECT
        g.INNERCODE,
        DATE(q.TRADINGDAY) AS trade_date,
        q.TURNOVERVALUE,
        q.CLOSEPRICE,
        ROW_NUMBER() OVER (
            PARTITION BY g.INNERCODE, DATE(q.TRADINGDAY)
            ORDER BY q.JSID DESC
        ) AS rn_ver
    FROM gem_stocks g
    JOIN qt_dailyquote q
      ON q.INNERCODE = g.INNERCODE
    JOIN window_dates wd
      ON DATE(q.TRADINGDAY) = wd.trade_date
    WHERE q.TURNOVERVALUE IS NOT NULL
      AND q.CLOSEPRICE IS NOT NULL
      AND q.CLOSEPRICE > 0
),
quote_ranked AS (
    SELECT q.INNERCODE, q.trade_date, q.TURNOVERVALUE, q.CLOSEPRICE, wd.rn_desc
    FROM quote_versions q
    JOIN window_dates wd ON wd.trade_date = q.trade_date
    WHERE q.rn_ver = 1
),
liquidity AS (
    SELECT
        INNERCODE,
        AVG(TURNOVERVALUE) AS avg_turnover_value_20d,
        COUNT(*) AS turnover_days
    FROM quote_ranked
    WHERE rn_desc <= 20
    GROUP BY INNERCODE
    HAVING COUNT(*) = 20
       AND AVG(TURNOVERVALUE) >= 50000000
),
price_20_chrono AS (
    SELECT
        INNERCODE,
        trade_date,
        CLOSEPRICE,
        MAX(CLOSEPRICE) OVER (
            PARTITION BY INNERCODE
            ORDER BY trade_date ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS running_high
    FROM quote_ranked
    WHERE rn_desc <= 20
),
drawdown_20 AS (
    SELECT
        INNERCODE,
        MIN(CLOSEPRICE / NULLIF(running_high, 0) - 1) AS max_drawdown_20d
    FROM price_20_chrono
    GROUP BY INNERCODE
),
valuation_asof AS (
    SELECT
        INNERCODE,
        NEGOTIABLEMV
    FROM (
        SELECT
            d.INNERCODE,
            d.NEGOTIABLEMV,
            ROW_NUMBER() OVER (
                PARTITION BY d.INNERCODE
                ORDER BY d.JSID DESC, d.ID DESC
            ) AS rn
        FROM lc_dindicesforvaluation d
        JOIN gem_stocks g
          ON g.INNERCODE = d.INNERCODE
        JOIN trade_asof ta
          ON d.TRADINGDAY = ta.trade_date
        WHERE d.NEGOTIABLEMV IS NOT NULL
          AND d.NEGOTIABLEMV > 0
    ) t
    WHERE rn = 1
),
turnover_stats AS (
    SELECT
        q.INNERCODE,
        AVG(CASE WHEN q.rn_desc <= 20 THEN q.TURNOVERVALUE / NULLIF(v.NEGOTIABLEMV, 0) * 100 END) AS avg_turnover_rate_20d,
        AVG(CASE WHEN q.rn_desc <= 120 THEN q.TURNOVERVALUE / NULLIF(v.NEGOTIABLEMV, 0) * 100 END) AS avg_turnover_rate_120d,
        SUM(CASE WHEN q.rn_desc <= 20 THEN 1 ELSE 0 END) AS cnt20,
        SUM(CASE WHEN q.rn_desc <= 120 THEN 1 ELSE 0 END) AS cnt120
    FROM quote_ranked q
    JOIN valuation_asof v
      ON v.INNERCODE = q.INNERCODE
    WHERE q.rn_desc <= 120
    GROUP BY q.INNERCODE
    HAVING cnt20 = 20
       AND cnt120 = 120
),
qfin_ver AS (
    SELECT
        q.COMPANYCODE,
        DATE(q.ENDDATE) AS end_date,
        q.OPERATINGREVENUEYOY,
        q.NPPARENTCOMPANYCUTYOY,
        ROW_NUMBER() OVER (
            PARTITION BY q.COMPANYCODE, DATE(q.ENDDATE)
            ORDER BY CASE q.MARK
                         WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                         WHEN 6 THEN 4 WHEN 2 THEN 5 ELSE 9
                     END,
                     q.INFOPUBLDATE DESC,
                     COALESCE(q.INSERTTIME, q.INFOPUBLDATE) DESC,
                     q.JSID DESC,
                     q.ID DESC
        ) AS rn_ver
    FROM lc_qfinancialindexnew q
    JOIN gem_stocks g
      ON g.COMPANYCODE = q.COMPANYCODE
    JOIN params p
      ON q.INFOPUBLDATE <= p.as_of_dt
    WHERE q.ENDDATE <= p.as_of_dt
      AND MONTH(q.ENDDATE) IN (3, 6, 9, 12)
      AND q.MARK IN (1, 8, 7, 6, 2)
),
latest_qfin AS (
    SELECT
        COMPANYCODE,
        OPERATINGREVENUEYOY AS revenue_yoy,
        NPPARENTCOMPANYCUTYOY AS cut_np_yoy
    FROM (
        SELECT
            COMPANYCODE,
            end_date,
            OPERATINGREVENUEYOY,
            NPPARENTCOMPANYCUTYOY,
            ROW_NUMBER() OVER (
                PARTITION BY COMPANYCODE
                ORDER BY end_date DESC
            ) AS rn_desc
        FROM qfin_ver
        WHERE rn_ver = 1
          AND OPERATINGREVENUEYOY IS NOT NULL
          AND NPPARENTCOMPANYCUTYOY IS NOT NULL
    ) t
    WHERE rn_desc = 1
),
eligible_base AS (
    SELECT
        g.SECUCODE,
        g.stock_name,
        lq.revenue_yoy,
        lq.cut_np_yoy,
        ts.avg_turnover_rate_20d,
        ts.avg_turnover_rate_120d,
        dd.max_drawdown_20d,
        v.NEGOTIABLEMV
    FROM gem_stocks g
    JOIN liquidity li
      ON li.INNERCODE = g.INNERCODE
    JOIN valuation_asof v
      ON v.INNERCODE = g.INNERCODE
    JOIN latest_qfin lq
      ON lq.COMPANYCODE = g.COMPANYCODE
    JOIN turnover_stats ts
      ON ts.INNERCODE = g.INNERCODE
    JOIN drawdown_20 dd
      ON dd.INNERCODE = g.INNERCODE
)
SELECT
    SECUCODE AS `股票代码`,
    stock_name AS `股票简称`,
    ROUND(revenue_yoy, 2) AS `营业收入同比`,
    ROUND(cut_np_yoy, 2) AS `扣非归母净利润同比`,
    ROUND(avg_turnover_rate_20d / NULLIF(avg_turnover_rate_120d, 0) * 100, 2) AS `换手降温比`,
    ROUND(-max_drawdown_20d * 100, 2) AS `近20日最大回撤幅度`,
    ROUND(NEGOTIABLEMV / 100000000, 2) AS `流通市值`
FROM eligible_base
WHERE revenue_yoy > 0
  AND cut_np_yoy > 0
  AND avg_turnover_rate_20d < avg_turnover_rate_120d
  AND max_drawdown_20d >= -0.25
ORDER BY avg_turnover_rate_20d / NULLIF(avg_turnover_rate_120d, 0) ASC,
         SECUCODE ASC
LIMIT 20;
