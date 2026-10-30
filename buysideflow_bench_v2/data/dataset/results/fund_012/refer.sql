WITH params AS (
    SELECT
        DATE('2024-06-07') AS as_of_date,
        DATE_SUB(DATE('2024-06-07'), INTERVAL 1 YEAR) AS start_date,
        DATE_ADD(DATE('2024-06-07'), INTERVAL 1 DAY) AS as_of_next_day
),
type_asof AS (
    SELECT
        ranked.INNERCODE,
        ranked.FIRSTASSETCATCODE,
        ranked.SECASSETCATCODE,
        ranked.THIRDASSETCATCODE
    FROM (
        SELECT
            t.INNERCODE,
            t.FIRSTASSETCATCODE,
            t.SECASSETCATCODE,
            t.THIRDASSETCATCODE,
            ROW_NUMBER() OVER (
                PARTITION BY t.INNERCODE
                ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
            ) AS rn
        FROM mf_jyfundtype t
        JOIN params p
          ON 1 = 1
        WHERE t.STANDARD = 75
          AND t.EFFECTIVEDATE <= p.as_of_date
          AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.as_of_date)
          AND t.INSERTTIME < p.as_of_next_day
    ) ranked
    WHERE ranked.rn = 1
),
fund_pool AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        COALESCE(sm.SECUABBR, fa.SECURITYCODE) AS fund_name
    FROM mf_fundarchives fa
    JOIN type_asof ta
      ON ta.INNERCODE = fa.INNERCODE
    LEFT JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN params p
      ON 1 = 1
    WHERE fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= DATE_SUB(p.as_of_date, INTERVAL 1 YEAR)
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.as_of_date)
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND fa.INVESTMENTTYPE = 16
      AND ta.FIRSTASSETCATCODE = 11
      AND ta.SECASSETCATCODE <> 1102
      AND ta.THIRDASSETCATCODE NOT IN (110201, 110202)
),
market_days AS (
    SELECT DISTINCT DATE(td.TRADINGDATE) AS nav_date
    FROM qt_tradingdaynew td
    JOIN params p
      ON td.TRADINGDATE >= p.start_date
     AND td.TRADINGDATE <= p.as_of_date
    WHERE td.SECUMARKET IN (83, 90)
      AND td.IFTRADINGDAY = 1
),
nav_dedup AS (
    SELECT
        fp.INNERCODE,
        fp.fund_code,
        fp.fund_name,
        DATE(n.TRADINGDAY) AS nav_date,
        n.UNITNVRESTORED AS adj_nav,
        ROW_NUMBER() OVER (
            PARTITION BY fp.INNERCODE, DATE(n.TRADINGDAY)
            ORDER BY n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM fund_pool fp
    JOIN mf_fundnetvalueretrans n
      ON n.INNERCODE = fp.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE n.TRADINGDAY >= p.start_date
      AND n.TRADINGDAY <= p.as_of_date
      AND n.INSERTTIME < p.as_of_next_day
),
nav_series AS (
    SELECT
        fp.INNERCODE,
        fp.fund_code,
        fp.fund_name,
        md.nav_date,
        CASE WHEN nd.adj_nav > 0 THEN nd.adj_nav END AS adj_nav
    FROM fund_pool fp
    CROSS JOIN market_days md
    LEFT JOIN nav_dedup nd
      ON nd.INNERCODE = fp.INNERCODE
     AND nd.nav_date = md.nav_date
     AND nd.rn = 1
),
daily_return AS (
    SELECT
        INNERCODE,
        fund_code,
        fund_name,
        nav_date,
        adj_nav / LAG(adj_nav) OVER (
            PARTITION BY INNERCODE
            ORDER BY nav_date
        ) - 1 AS daily_return
    FROM nav_series
),
metrics AS (
    SELECT
        dr.INNERCODE,
        dr.fund_code,
        dr.fund_name,
        COUNT(*) AS valid_return_days,
        EXP(SUM(LOG(1.0 + dr.daily_return)) * 252.0 / COUNT(*)) - 1.0 AS annual_return,
        STDDEV_POP(dr.daily_return) * SQRT(252) AS annual_volatility,
        AVG(dr.daily_return) / NULLIF(STDDEV_POP(dr.daily_return), 0) * SQRT(252) AS sharpe
    FROM daily_return dr
    WHERE dr.daily_return IS NOT NULL
    GROUP BY dr.INNERCODE, dr.fund_code, dr.fund_name
    HAVING COUNT(*) >= 200
)
SELECT
    fund_code AS `基金代码`,
    fund_name AS `基金简称`,
    ROUND(annual_return * 100, 2) AS `近一年年化收益`,
    ROUND(annual_volatility * 100, 2) AS `年化波动`,
    ROUND(sharpe, 4) AS `夏普`
FROM metrics
WHERE sharpe IS NOT NULL
ORDER BY sharpe DESC, fund_code ASC
LIMIT 10;
