WITH params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
trade_asof AS (
    SELECT MAX(t.TRADINGDATE) AS trade_day
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE < p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET IN (83, 90)
),
trade_calendar AS (
    SELECT
        d.TRADINGDATE,
        ROW_NUMBER() OVER (ORDER BY d.TRADINGDATE) AS trade_seq
    FROM (
        SELECT DISTINCT t.TRADINGDATE
        FROM qt_tradingdaynew t
        JOIN params p
          ON t.TRADINGDATE <= p.as_of_date
        WHERE t.IFTRADINGDAY = 1
          AND t.SECUMARKET IN (83, 90)
    ) d
),
latest_trade_seq AS (
    SELECT MAX(trade_seq) AS asof_trade_seq
    FROM trade_calendar
),
listing_cutoff AS (
    SELECT tc.TRADINGDATE AS cutoff_trade_day
    FROM trade_calendar tc
    JOIN latest_trade_seq lts
      ON tc.trade_seq = lts.asof_trade_seq - 249
),
stock_base AS (
    SELECT
        s.INNERCODE,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS company
    FROM secumain s
    JOIN listing_cutoff lc
      ON DATE(s.LISTEDDATE) <= lc.cutoff_trade_day
    CROSS JOIN params p
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR IN (1, 2, 6)
      AND NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = s.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND ra.IMPLEMENTDATE <= p.as_of_date
            AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE)
                < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
            AND (
                ra.REMOVEDATE IS NULL
                OR ra.REMOVEDATE > p.as_of_date
                OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE)
                    >= DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
            )
      )
      AND NOT EXISTS (  -- 观察日按 lc_liststatus 已终止上市或摘牌者不入样本；退市整理期仍属上市
          SELECT 1
          FROM lc_liststatus ls
          WHERE ls.INNERCODE = s.INNERCODE
            AND ls.CHANGETYPE IN (4, 5)
            AND ls.CHANGEDATE <= p.as_of_date
      )
),
valuation_asof AS (
    SELECT
        x.SECUCODE,
        x.company,
        x.TOTALMV,
        x.NEGOTIABLEMV
    FROM (
        SELECT
            sb.SECUCODE,
            sb.company,
            v.TOTALMV,
            v.NEGOTIABLEMV,
            ROW_NUMBER() OVER (PARTITION BY sb.INNERCODE ORDER BY v.JSID DESC) AS rn
        FROM stock_base sb
        JOIN lc_dindicesforvaluation v
          ON v.INNERCODE = sb.INNERCODE
        JOIN trade_asof ta
          ON v.TRADINGDAY = ta.trade_day
        WHERE v.TOTALMV > 0
          AND v.NEGOTIABLEMV > 0
    ) x
    WHERE x.rn = 1
),
stats AS (
    SELECT
        AVG(LN(TOTALMV)) AS avg_log_mv,
        STDDEV_POP(LN(TOTALMV)) AS std_log_mv
    FROM valuation_asof
),
scored AS (
    SELECT
        va.SECUCODE,
        va.company,
        va.NEGOTIABLEMV,
        -POW((LN(va.TOTALMV) - s.avg_log_mv) / NULLIF(s.std_log_mv, 0), 2) AS nonlinear_mv_factor
    FROM valuation_asof va
    CROSS JOIN stats s
    WHERE s.std_log_mv IS NOT NULL
      AND s.std_log_mv > 0
),
ranked AS (
    SELECT
        *,
        PERCENT_RANK() OVER (ORDER BY nonlinear_mv_factor DESC) AS factor_pct
    FROM scored
)
SELECT
    SECUCODE AS `股票代码`,
    company AS `股票简称`,
    ROUND(nonlinear_mv_factor, 6) AS `非线性市值因子`,
    ROUND(factor_pct * 100, 2) AS `因子分位`,
    ROUND(NEGOTIABLEMV / 100000000, 2) AS `流通市值(亿元)`
FROM ranked
WHERE factor_pct <= 0.10
ORDER BY NEGOTIABLEMV ASC, SECUCODE
LIMIT 5;
