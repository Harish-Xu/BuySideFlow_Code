-- name: trading_days
SELECT DISTINCT DATE(td.TRADINGDATE) AS trade_date
FROM qt_tradingdaynew td
WHERE td.IFTRADINGDAY = 1
  AND td.SECUMARKET IN (83, 90)
  AND td.TRADINGDATE BETWEEN DATE('${calendar_start_date}') AND DATE('${calendar_end_date}')
ORDER BY trade_date;

-- name: month_ends
SELECT MAX(DATE(td.TRADINGDATE)) AS obs_date
FROM qt_tradingdaynew td
WHERE td.IFTRADINGDAY = 1
  AND td.SECUMARKET IN (83, 90)
  AND td.TRADINGDATE BETWEEN DATE('${signal_start_date}') AND DATE('${signal_end_date}')
  AND MONTH(td.TRADINGDATE) = 4
GROUP BY YEAR(td.TRADINGDATE)
ORDER BY obs_date;

-- name: candidates
WITH
params AS (
    SELECT DATE('${obs_date}') AS obs_date
),
stock_raw AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        LPAD(s.SECUCODE, 6, '0') AS stock_code,
        COALESCE(NULLIF(s.CHINAMEABBR, ''), NULLIF(s.SECUABBR, ''), NULLIF(s.CHINAME, ''), s.SECUCODE) AS stock_name,
        ROW_NUMBER() OVER (
            PARTITION BY s.COMPANYCODE
            ORDER BY
                CASE WHEN s.LISTEDSECTOR = 1 THEN 0 ELSE 1 END,
                s.SECUCODE,
                s.INNERCODE
        ) AS rn
    FROM secumain s
    JOIN params p
      ON s.LISTEDDATE <= p.obs_date
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR IN (1, 2, 6)
      AND s.COMPANYCODE IS NOT NULL
      AND (
          SELECT ls.CHANGETYPE
          FROM lc_liststatus ls
          WHERE ls.INNERCODE = s.INNERCODE
            AND ls.SECUMARKET IN (83, 90)
            AND ls.CHANGETYPE BETWEEN 1 AND 9
            AND DATE(ls.CHANGEDATE) <= p.obs_date
            AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) < DATE_ADD(p.obs_date, INTERVAL 1 DAY)
          ORDER BY
            ls.CHANGEDATE DESC,
            COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
            ls.JSID DESC
          LIMIT 1
      ) IN (1, 3)
      AND NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = s.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND DATE(ra.IMPLEMENTDATE) <= p.obs_date
            AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE)
                < DATE_ADD(p.obs_date, INTERVAL 1 DAY)
            AND (
                ra.REMOVEDATE IS NULL
                OR DATE(ra.REMOVEDATE) > p.obs_date
                OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE)
                    >= DATE_ADD(p.obs_date, INTERVAL 1 DAY)
            )
      )
),
stock_base AS (
    SELECT INNERCODE, COMPANYCODE, stock_code, stock_name
    FROM stock_raw
    WHERE rn = 1
),
industry_raw AS (
    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYNAME AS industry_name,
        ROW_NUMBER() OVER (
            PARTITION BY e.COMPANYCODE
            ORDER BY
                e.INFOPUBLDATE DESC,
                COALESCE(e.CANCELDATE, DATE('9999-12-31')) DESC,
                e.JSID DESC
        ) AS rn
    FROM lc_exgindustry e
    JOIN stock_base sb
      ON sb.COMPANYCODE = e.COMPANYCODE
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.obs_date, INTERVAL 1 DAY)
    WHERE e.STANDARD = 38
      AND e.FIRSTINDUSTRYNAME IS NOT NULL
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > (SELECT obs_date FROM params))
),
industry AS (
    SELECT COMPANYCODE, industry_name
    FROM industry_raw
    WHERE rn = 1
      AND industry_name NOT IN ('银行', '非银金融', '房地产')
),
valuation_raw AS (
    SELECT
        v.INNERCODE,
        v.TOTALMV AS market_cap,
        v.NEGOTIABLEMV AS float_market_cap,
        v.DIVIDENDRATIO AS dividend_yield,
        ROW_NUMBER() OVER (
            PARTITION BY v.INNERCODE
            ORDER BY COALESCE(v.INSERTTIME, v.TRADINGDAY) DESC, v.JSID DESC
        ) AS rn
    FROM lc_dindicesforvaluation v
    JOIN stock_base sb
      ON sb.INNERCODE = v.INNERCODE
    JOIN params p
      ON v.TRADINGDAY = p.obs_date
    WHERE v.TOTALMV IS NOT NULL
      AND v.TOTALMV > 0
      AND v.DIVIDENDRATIO IS NOT NULL
      AND v.DIVIDENDRATIO > 0
),
valuation AS (
    SELECT INNERCODE, market_cap, float_market_cap, dividend_yield
    FROM valuation_raw
    WHERE rn = 1
),
annual_raw AS (
    SELECT
        f.COMPANYCODE,
        f.ENDDATE AS end_date,
        f.NETOPERATECASHFLOW,
        f.NETPROFITCUT,
        ROW_NUMBER() OVER (
            PARTITION BY f.COMPANYCODE, f.ENDDATE
            ORDER BY
                CASE f.MARK
                    WHEN 1 THEN 0
                    WHEN 8 THEN 1
                    WHEN 7 THEN 2
                    WHEN 6 THEN 3
                    WHEN 2 THEN 4
                    ELSE 9
                END,
                f.INFOPUBLDATE DESC,
                COALESCE(f.INSERTTIME, f.INFOPUBLDATE) DESC,
                f.JSID DESC
        ) AS rn
    FROM lc_maindatanew f
    JOIN stock_base sb
      ON sb.COMPANYCODE = f.COMPANYCODE
    JOIN params p
      ON f.INFOPUBLDATE < DATE_ADD(p.obs_date, INTERVAL 1 DAY)
    WHERE f.MARK IN (1, 8, 7, 6, 2)
      AND f.ACCOUNTINGSTANDARDS = 1
      AND DATE_FORMAT(f.ENDDATE, '%m-%d') = '12-31'
      AND YEAR(f.ENDDATE) BETWEEN YEAR((SELECT obs_date FROM params)) - 3
                              AND YEAR((SELECT obs_date FROM params)) - 1
      AND f.NETOPERATECASHFLOW IS NOT NULL
      AND f.NETPROFITCUT IS NOT NULL
),
annual_ranked AS (
    SELECT
        COMPANYCODE,
        end_date,
        NETOPERATECASHFLOW,
        NETPROFITCUT,
        ROW_NUMBER() OVER (PARTITION BY COMPANYCODE ORDER BY end_date DESC) AS year_rank
    FROM annual_raw
    WHERE rn = 1
),
annual_latest AS (
    SELECT COMPANYCODE, end_date, NETOPERATECASHFLOW, NETPROFITCUT
    FROM annual_ranked
    WHERE year_rank <= 3
),
annual_stats AS (
    SELECT
        COMPANYCODE,
        COUNT(*) AS annual_count,
        SUM(NETOPERATECASHFLOW) AS cfo_sum_3y,
        SUM(CASE WHEN NETPROFITCUT > 0 THEN 1 ELSE 0 END) AS positive_profit_years_3y
    FROM annual_latest
    GROUP BY COMPANYCODE
    HAVING COUNT(*) = 3
),
dividend_3y_raw AS (
    SELECT
        sb.COMPANYCODE,
        d.INNERCODE,
        d.ENDDATE AS end_date,
        COALESCE(d.PROPOSALSN, 0) AS proposal_sn,
        d.TOTALCASHDIVICOMRMB,
        ROW_NUMBER() OVER (
            PARTITION BY d.INNERCODE, d.ENDDATE, COALESCE(d.PROPOSALSN, 0)
            ORDER BY
                COALESCE(d.ADVANCEDATE, d.SMDECIPUBLDATE, d.DIVIDENDIMPLEMENTDATE, d.LATESTINFOPUBLDATE) DESC,
                d.JSID DESC
        ) AS rn
    FROM lc_dividend d
    JOIN stock_base sb
      ON sb.INNERCODE = d.INNERCODE
    JOIN annual_latest a
      ON a.COMPANYCODE = sb.COMPANYCODE
     AND a.end_date = d.ENDDATE
    JOIN params p
      ON COALESCE(d.ADVANCEDATE, d.SMDECIPUBLDATE, d.DIVIDENDIMPLEMENTDATE, d.LATESTINFOPUBLDATE)
            < DATE_ADD(p.obs_date, INTERVAL 1 DAY)
    WHERE d.TOTALCASHDIVICOMRMB IS NOT NULL
      AND (d.IFDIVIDEND IS NULL OR d.IFDIVIDEND = 1)
),
dividend_3y AS (
    SELECT COMPANYCODE, SUM(TOTALCASHDIVICOMRMB) AS cash_dividend_sum_3y
    FROM dividend_3y_raw
    WHERE rn = 1
    GROUP BY COMPANYCODE
),
debt_raw AS (
    SELECT
        m.COMPANYCODE,
        m.DEBTASSETSRATIO AS debt_ratio,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE
            ORDER BY
                m.ENDDATE DESC,
                m.INFOPUBLDATE DESC,
                COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                m.JSID DESC
        ) AS rn
    FROM lc_mainindexnew m
    JOIN stock_base sb
      ON sb.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.INFOPUBLDATE < DATE_ADD(p.obs_date, INTERVAL 1 DAY)
     AND m.ENDDATE <= p.obs_date
    WHERE m.DEBTASSETSRATIO IS NOT NULL
      AND DATE_FORMAT(m.ENDDATE, '%m-%d') = '12-31'
)
SELECT
    p.obs_date,
    sb.INNERCODE,
    sb.COMPANYCODE,
    sb.stock_code,
    sb.stock_name,
    i.industry_name,
    v.market_cap,
    v.float_market_cap,
    d3.cash_dividend_sum_3y,
    a.cfo_sum_3y,
    a.positive_profit_years_3y,
    de.debt_ratio,
    v.dividend_yield,
    a.cfo_sum_3y / NULLIF(d3.cash_dividend_sum_3y, 0) AS ocf_dividend_cover_3y
FROM stock_base sb
JOIN params p
  ON 1 = 1
JOIN industry i
  ON i.COMPANYCODE = sb.COMPANYCODE
JOIN valuation v
  ON v.INNERCODE = sb.INNERCODE
JOIN annual_stats a
  ON a.COMPANYCODE = sb.COMPANYCODE
JOIN dividend_3y d3
  ON d3.COMPANYCODE = sb.COMPANYCODE
JOIN debt_raw de
  ON de.COMPANYCODE = sb.COMPANYCODE
 AND de.rn = 1
WHERE d3.cash_dividend_sum_3y > 0;

-- name: stock_prices
SELECT
    q.INNERCODE,
    DATE(q.TRADINGDAY) AS trade_date,
    q.OPENPRICE,
    q.CLOSEPRICE,
    p.BACKWARDPRICE
FROM qt_dailyquote q
JOIN qt_performancedata p
  ON p.INNERCODE = q.INNERCODE
 AND p.TRADINGDAY = q.TRADINGDAY
WHERE q.INNERCODE IN (${inner_values})
  AND q.TRADINGDAY IN (${price_dates})
  AND q.OPENPRICE IS NOT NULL
  AND q.OPENPRICE > 0
  AND q.CLOSEPRICE IS NOT NULL
  AND q.CLOSEPRICE > 0
  AND p.BACKWARDPRICE IS NOT NULL
  AND p.BACKWARDPRICE > 0;

-- name: benchmark_prices
SELECT
    DATE(q.TRADINGDAY) AS trade_date,
    q.OPENPRICE,
    q.CLOSEPRICE
FROM secumain sm
JOIN qt_indexquote q
  ON q.INNERCODE = sm.INNERCODE
WHERE sm.SECUCATEGORY = 4
  AND sm.SECUCODE = '${benchmark_code}'
  AND q.TRADINGDAY IN (${price_dates})
  AND q.OPENPRICE IS NOT NULL
  AND q.OPENPRICE > 0
  AND q.CLOSEPRICE IS NOT NULL
  AND q.CLOSEPRICE > 0;
