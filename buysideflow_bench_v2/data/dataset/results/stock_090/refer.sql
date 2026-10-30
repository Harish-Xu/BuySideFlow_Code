WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
trade_asof AS (
    SELECT MAX(t.TRADINGDATE) AS trade_day
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET IN (83, 90)
),
stock_base AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name
    FROM (
        SELECT
            sm.*,
            ROW_NUMBER() OVER (
                PARTITION BY sm.COMPANYCODE
                ORDER BY
                    CASE WHEN sm.LISTEDSECTOR = 1 THEN 0 ELSE 1 END,
                    sm.INNERCODE
            ) AS rn
        FROM secumain sm
        CROSS JOIN trade_asof ta
	        WHERE sm.SECUCATEGORY = 1
	          AND sm.SECUMARKET IN (83, 90)
	          AND sm.LISTEDSECTOR IN (1, 2, 6)
	          AND sm.LISTEDDATE <= DATE_SUB(ta.trade_day, INTERVAL 3 YEAR)
	          AND NOT EXISTS (
	              SELECT 1
	              FROM cs_riskalert ra
	              WHERE ra.INNERCODE = sm.INNERCODE
	                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
	                AND ra.IMPLEMENTDATE <= ta.trade_day
	                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE)
	                    < DATE_ADD(ta.trade_day, INTERVAL 1 DAY)
	                AND (
	                    ra.REMOVEDATE IS NULL
	                    OR ra.REMOVEDATE > ta.trade_day
	                    OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE)
	                        >= DATE_ADD(ta.trade_day, INTERVAL 1 DAY)
	                )
	          )
	    ) s
	    WHERE s.rn = 1
	),
recent_60_trade_days AS (
    SELECT trading_day
    FROM (
        SELECT DISTINCT q.TRADINGDAY AS trading_day
        FROM qt_dailyquote q
        JOIN trade_asof ta
          ON q.TRADINGDAY <= ta.trade_day
        ORDER BY q.TRADINGDAY DESC
        LIMIT 60
    ) d
),
liquidity AS (
    SELECT
        sb.INNERCODE,
        AVG(q.TURNOVERVALUE) AS avg_turnover_value_60d,
        COUNT(*) AS quote_cnt_60d
    FROM stock_base sb
    JOIN qt_dailyquote q
      ON q.INNERCODE = sb.INNERCODE
    JOIN recent_60_trade_days d
      ON d.trading_day = q.TRADINGDAY
    WHERE q.TURNOVERVALUE IS NOT NULL
    GROUP BY sb.INNERCODE
    HAVING COUNT(*) = 60
       AND AVG(q.TURNOVERVALUE) >= 50000000
),
market_asof AS (
    SELECT
        sb.INNERCODE,
        q.CLOSEPRICE
    FROM stock_base sb
    JOIN trade_asof ta
    JOIN qt_dailyquote q
      ON q.INNERCODE = sb.INNERCODE
     AND q.TRADINGDAY = ta.trade_day
    WHERE q.CLOSEPRICE IS NOT NULL
      AND q.CLOSEPRICE > 0
      AND COALESCE(q.TURNOVERVALUE, 0) > 0
),
valuation_asof AS (
    SELECT
        v.INNERCODE,
        COALESCE(v.DIVIDENDRATIOLYR, v.DIVIDENDRATIOLYR2, v.DIVIDENDRATIO) AS dividend_yield,
        v.TOTALMV
    FROM lc_dindicesforvaluation v
    JOIN (
        SELECT
            v.INNERCODE,
            MAX(v.JSID) AS max_jsid
        FROM lc_dindicesforvaluation v
        JOIN trade_asof ta
          ON v.TRADINGDAY = ta.trade_day
        JOIN stock_base sb
          ON sb.INNERCODE = v.INNERCODE
        WHERE COALESCE(v.DIVIDENDRATIOLYR, v.DIVIDENDRATIOLYR2, v.DIVIDENDRATIO) IS NOT NULL
          AND v.TOTALMV IS NOT NULL
        GROUP BY v.INNERCODE
    ) k
      ON k.INNERCODE = v.INNERCODE
     AND k.max_jsid = v.JSID
),
base_sample AS (
    SELECT
        sb.INNERCODE,
        sb.COMPANYCODE,
        sb.SECUCODE,
        sb.stock_name,
        ma.CLOSEPRICE,
        va.dividend_yield,
        va.TOTALMV,
        PERCENT_RANK() OVER (ORDER BY va.dividend_yield DESC) AS dividend_rank
    FROM stock_base sb
    JOIN liquidity li
      ON li.INNERCODE = sb.INNERCODE
    JOIN market_asof ma
      ON ma.INNERCODE = sb.INNERCODE
    JOIN valuation_asof va
      ON va.INNERCODE = sb.INNERCODE
),
annual_payout_raw AS (
    SELECT
        m.COMPANYCODE,
        m.ENDDATE,
        m.DIVIDENDPAIDRATIO,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY m.INFOPUBLDATE DESC,
                     COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                     m.JSID DESC
        ) AS rn
    FROM lc_mainindexnew m
    JOIN base_sample b
      ON b.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.INFOPUBLDATE <= p.as_of_date
    WHERE m.ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
      AND m.DIVIDENDPAIDRATIO IS NOT NULL
),
annual_payout_stats AS (
    SELECT
        COMPANYCODE,
        COUNT(*) AS payout_years,
        AVG(DIVIDENDPAIDRATIO) AS avg_payout_ratio_3y,
        MIN(DIVIDENDPAIDRATIO) AS min_payout_ratio_3y,
        MAX(DIVIDENDPAIDRATIO) AS max_payout_ratio_3y
    FROM annual_payout_raw
    WHERE rn = 1
    GROUP BY COMPANYCODE
    HAVING COUNT(*) = 3
       AND MIN(DIVIDENDPAIDRATIO) >= 20
       AND MAX(DIVIDENDPAIDRATIO) <= 80
),
annual_cfo_raw AS (
    SELECT
        m.COMPANYCODE,
        m.ENDDATE,
        m.NETOPERATECASHFLOW,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY CASE m.MARK
                         WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                         WHEN 6 THEN 4 WHEN 2 THEN 5 ELSE 99 END,
                     m.INFOPUBLDATE DESC,
                     COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                     m.JSID DESC
        ) AS rn
    FROM lc_maindatanew m
    JOIN base_sample b
      ON b.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.INFOPUBLDATE <= p.as_of_date
    WHERE m.MARK IN (1, 8, 7, 6, 2)
      AND m.ACCOUNTINGSTANDARDS = 1
      AND m.ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
      AND m.NETOPERATECASHFLOW IS NOT NULL
),
annual_cfo_stats AS (
    SELECT
        COMPANYCODE,
        COUNT(*) AS cfo_years,
        SUM(NETOPERATECASHFLOW) AS cfo_sum_3y
    FROM annual_cfo_raw
    WHERE rn = 1
    GROUP BY COMPANYCODE
    HAVING COUNT(*) = 3
),
cash_dividend_event AS (
    SELECT
        d.INNERCODE,
        d.ENDDATE,
        d.TOTALCASHDIVICOMRMB,
        ROW_NUMBER() OVER (
            PARTITION BY d.INNERCODE, DATE(d.ENDDATE), COALESCE(d.PROPOSALSN, 0)
            ORDER BY
                COALESCE(d.ADVANCEDATE, d.SMDECIPUBLDATE, d.DIVIDENDIMPLEMENTDATE, d.LATESTINFOPUBLDATE) DESC,
                d.JSID DESC
        ) AS rn
    FROM lc_dividend d
    JOIN base_sample b
      ON b.INNERCODE = d.INNERCODE
    JOIN params p
      ON COALESCE(d.ADVANCEDATE, d.SMDECIPUBLDATE, d.DIVIDENDIMPLEMENTDATE, d.LATESTINFOPUBLDATE)
            < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE d.ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
      AND d.TOTALCASHDIVICOMRMB IS NOT NULL
      AND (d.IFDIVIDEND IS NULL OR d.IFDIVIDEND = 1)
),
cash_dividend_stats AS (
    SELECT
        sb.COMPANYCODE,
        COUNT(DISTINCT DATE(e.ENDDATE)) AS dividend_years,
        SUM(e.TOTALCASHDIVICOMRMB) AS cash_dividend_sum_3y
	    FROM cash_dividend_event e
	    JOIN stock_base sb
	      ON sb.INNERCODE = e.INNERCODE
	    WHERE e.rn = 1
	    GROUP BY sb.COMPANYCODE
	    HAVING COUNT(DISTINCT DATE(e.ENDDATE)) = 3
	)
SELECT
    ta.trade_day AS `观察日`,
    b.SECUCODE AS `股票代码`,
    b.stock_name AS `股票简称`,
    ROUND(b.CLOSEPRICE, 4) AS `收盘价`,
    ROUND(b.dividend_yield, 4) AS `股息率`,
    ROUND(ap.avg_payout_ratio_3y, 4) AS `近3年平均分红率`,
    ROUND(cd.cash_dividend_sum_3y / 100000000, 4) AS `近3年现金分红合计(亿元)`,
    ROUND(ac.cfo_sum_3y / 100000000, 4) AS `近3年经营现金流合计(亿元)`,
    ROUND(ac.cfo_sum_3y / NULLIF(cd.cash_dividend_sum_3y, 0), 4) AS `现金流覆盖倍数`,
    ROUND(b.TOTALMV / 100000000, 4) AS `总市值(亿元)`
FROM base_sample b
CROSS JOIN trade_asof ta
JOIN annual_payout_stats ap
  ON ap.COMPANYCODE = b.COMPANYCODE
JOIN annual_cfo_stats ac
  ON ac.COMPANYCODE = b.COMPANYCODE
JOIN cash_dividend_stats cd
  ON cd.COMPANYCODE = b.COMPANYCODE
WHERE b.CLOSEPRICE < 9
  AND b.dividend_rank <= 0.25
  AND cd.cash_dividend_sum_3y > 0
  AND ac.cfo_sum_3y > cd.cash_dividend_sum_3y
ORDER BY b.dividend_yield DESC, b.SECUCODE
LIMIT 20
