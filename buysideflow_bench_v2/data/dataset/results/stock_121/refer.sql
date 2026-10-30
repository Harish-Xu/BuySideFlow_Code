WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
latest_week_end AS (
    SELECT MAX(week_end) AS trading_day
    FROM (
        SELECT
            YEARWEEK(td.TRADINGDATE, 1) AS year_week,
            MAX(td.TRADINGDATE) AS week_end,
            DATE_ADD(
                DATE_SUB(MIN(td.TRADINGDATE), INTERVAL WEEKDAY(MIN(td.TRADINGDATE)) DAY),
                INTERVAL 4 DAY
            ) AS calendar_friday
        FROM qt_tradingdaynew td
        JOIN params p
          ON td.TRADINGDATE <= p.as_of_date
        WHERE td.IFTRADINGDAY = 1
          AND td.SECUMARKET = 83
        GROUP BY YEARWEEK(td.TRADINGDATE, 1)
    ) w
    JOIN params p
      ON w.calendar_friday <= p.as_of_date
),
trade_days AS (
    SELECT TRADINGDATE
    FROM (
        SELECT
            td.TRADINGDATE,
            ROW_NUMBER() OVER (ORDER BY td.TRADINGDATE DESC) AS rn_desc
        FROM qt_tradingdaynew td
        JOIN latest_week_end lwe
          ON td.TRADINGDATE <= lwe.trading_day
        WHERE td.IFTRADINGDAY = 1
          AND td.SECUMARKET = 83
    ) t
    WHERE rn_desc <= 20
),
stock_base AS (
    SELECT COMPANYCODE, INNERCODE
    FROM (
        SELECT
            s.COMPANYCODE,
            s.INNERCODE,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.INNERCODE
            ) AS rn
        FROM secumain s
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2, 6)
    ) t
    WHERE rn = 1
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
        JOIN latest_week_end lwe
          ON e.INFOPUBLDATE < DATE_ADD(lwe.trading_day, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > lwe.trading_day)
    ) t
    WHERE rn = 1
),
price_window AS (
    SELECT
        sb.INNERCODE,
        ia.FIRSTINDUSTRYCODE,
        ia.FIRSTINDUSTRYNAME,
        q.TRADINGDAY,
        q.CLOSEPRICE
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    JOIN qt_dailyquote q
      ON q.INNERCODE = sb.INNERCODE
    JOIN trade_days td
      ON td.TRADINGDATE = q.TRADINGDAY
    WHERE q.CLOSEPRICE IS NOT NULL
),
ma_calc AS (
    SELECT
        INNERCODE,
        FIRSTINDUSTRYCODE,
        FIRSTINDUSTRYNAME,
        TRADINGDAY,
        CLOSEPRICE,
        AVG(CLOSEPRICE) OVER (
            PARTITION BY INNERCODE
            ORDER BY TRADINGDAY
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) AS ma20,
        COUNT(CLOSEPRICE) OVER (
            PARTITION BY INNERCODE
            ORDER BY TRADINGDAY
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) AS ma20_count
    FROM price_window
),
latest_snapshot AS (
    SELECT
        m.FIRSTINDUSTRYCODE,
        m.FIRSTINDUSTRYNAME,
        CASE WHEN m.CLOSEPRICE > m.ma20 THEN 1 ELSE 0 END AS above_ma20
    FROM ma_calc m
    JOIN latest_week_end lwe
      ON m.TRADINGDAY = lwe.trading_day
    WHERE m.ma20_count = 20
),
industry_ratio AS (
    SELECT
        FIRSTINDUSTRYCODE,
        FIRSTINDUSTRYNAME,
        AVG(above_ma20) AS breadth_ratio
    FROM latest_snapshot
    GROUP BY FIRSTINDUSTRYCODE, FIRSTINDUSTRYNAME
),
ranked AS (
    SELECT
        FIRSTINDUSTRYCODE,
        FIRSTINDUSTRYNAME,
        breadth_ratio,
        ROW_NUMBER() OVER (ORDER BY breadth_ratio DESC, FIRSTINDUSTRYCODE) AS rn
    FROM industry_ratio
)
SELECT
    lwe.trading_day AS `最新计算日期`,
    CASE
        WHEN SUM(
            CASE
                WHEN r.rn <= 3
                 AND r.FIRSTINDUSTRYNAME IN ('银行', '有色金属', '钢铁', '煤炭')
                THEN 1 ELSE 0
            END
        ) = 0
        THEN 'BUY_SMALL_CAP'
        ELSE 'SELL_SMALL_CAP'
    END AS `信号`
FROM ranked r
CROSS JOIN latest_week_end lwe
GROUP BY lwe.trading_day;
