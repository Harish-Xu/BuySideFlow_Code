WITH
params AS (
    SELECT DATE('2025-05-06') AS as_of_date, TIMESTAMP('2025-05-07') AS cutoff
),
listed_asof AS (
    SELECT INNERCODE, CHANGETYPE
    FROM (
        SELECT ls.INNERCODE, ls.CHANGETYPE,
               ROW_NUMBER() OVER (PARTITION BY ls.INNERCODE
                   ORDER BY ls.CHANGEDATE DESC, COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
                            ls.JSID DESC, ls.ID DESC) AS rn
        FROM lc_liststatus ls JOIN params p
          ON ls.CHANGEDATE < p.cutoff
         AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) < p.cutoff
        WHERE ls.SECUMARKET IN (83, 90) AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) s WHERE rn = 1
),
industry_asof AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME
    FROM (
        SELECT e.COMPANYCODE, e.FIRSTINDUSTRYNAME,
               ROW_NUMBER() OVER (PARTITION BY e.COMPANYCODE
                   ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC, e.ID DESC) AS rn
        FROM lc_exgindustry e JOIN params p ON e.INFOPUBLDATE < p.cutoff
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    ) e WHERE rn = 1
),
stock_base AS (
    SELECT INNERCODE, COMPANYCODE, SECUCODE, stock_name
    FROM (
        SELECT sm.INNERCODE, sm.COMPANYCODE, sm.SECUCODE,
               COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, sm.SECUCODE) AS stock_name,
               ROW_NUMBER() OVER (PARTITION BY sm.COMPANYCODE
                   ORDER BY sm.INNERCODE) AS rn
        FROM secumain sm
        JOIN params p ON sm.LISTEDDATE <= DATE_SUB(p.as_of_date, INTERVAL 1 YEAR)
        JOIN listed_asof ls ON ls.INNERCODE = sm.INNERCODE AND ls.CHANGETYPE IN (1, 2, 3, 6)
        LEFT JOIN industry_asof ia ON ia.COMPANYCODE = sm.COMPANYCODE
        WHERE sm.SECUCATEGORY = 1 AND sm.SECUMARKET IN (83, 90)
          AND sm.LISTEDSECTOR IN (1, 2, 6)
          AND (ia.FIRSTINDUSTRYNAME IS NULL OR ia.FIRSTINDUSTRYNAME NOT IN ('银行', '非银金融'))
          AND NOT EXISTS (
              SELECT 1 FROM cs_riskalert ra
              WHERE ra.INNERCODE = sm.INNERCODE AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND ra.IMPLEMENTDATE <= p.as_of_date
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) < p.cutoff
                AND (ra.REMOVEDATE IS NULL OR ra.REMOVEDATE > p.as_of_date
                     OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) >= p.cutoff)
          )
    ) s WHERE rn = 1
),
recent_20_trade_days AS (
    SELECT DISTINCT t.TRADINGDATE AS trading_day
    FROM qt_tradingdaynew t JOIN params p
      ON t.TRADINGDATE BETWEEN DATE_SUB(p.as_of_date, INTERVAL 3 MONTH) AND p.as_of_date
    WHERE t.IFTRADINGDAY = 1 AND t.SECUMARKET IN (83, 90)
    ORDER BY t.TRADINGDATE DESC LIMIT 20
),
quote_ranked AS (
    SELECT q.INNERCODE, q.TRADINGDAY, q.TURNOVERVALUE,
           ROW_NUMBER() OVER (PARTITION BY q.INNERCODE, q.TRADINGDAY
               ORDER BY q.JSID DESC, q.ID DESC) AS rn
    FROM qt_dailyquote q JOIN stock_base b ON b.INNERCODE = q.INNERCODE
    JOIN recent_20_trade_days d ON d.trading_day = q.TRADINGDAY
),
daily_turnover AS (
    SELECT b.INNERCODE, d.trading_day,
           CASE WHEN EXISTS (
               SELECT 1 FROM lc_suspendresumption sr JOIN params p
                 ON sr.INFOPUBLDATE < p.cutoff
               WHERE sr.INNERCODE = b.INNERCODE
                 AND sr.SUSPENDDATE <= d.trading_day
                 AND (sr.SUSPENDDATE < d.trading_day OR TIME(sr.SUSPENDTIME) <= TIME('09:30:00'))
                 AND ((sr.SUSPENDTYPE = 50 AND sr.SUSPENDDATE = d.trading_day
                       AND (sr.RESUMPTIONDATE IS NULL OR sr.RESUMPTIONDATE > d.trading_day))
                      OR (sr.SUSPENDTYPE = 30
                          AND (sr.RESUMPTIONDATE IS NULL OR sr.RESUMPTIONDATE > d.trading_day)))
           ) THEN 0 ELSE q.TURNOVERVALUE END AS turnover
    FROM stock_base b CROSS JOIN recent_20_trade_days d
    LEFT JOIN quote_ranked q ON q.INNERCODE = b.INNERCODE
      AND q.TRADINGDAY = d.trading_day AND q.rn = 1
),
liquidity AS (
    SELECT INNERCODE, SUM(turnover) / 20 AS avg_turnover_value_20d
    FROM daily_turnover GROUP BY INNERCODE
    HAVING COUNT(turnover) = 20 AND SUM(turnover) / 20 >= 50000000
),
financial_ranked AS (
    SELECT m.COMPANYCODE, m.ENDDATE, m.INFOPUBLDATE, m.MARK, m.JSID, m.ID,
           m.NETPROFIT, m.TOTALASSETS, m.NETOPERATECASHFLOW, m.OPERATINGREENUE,
           m.SURPLUSRESERVEFUND, m.RETAINEDPROFIT,
           ROW_NUMBER() OVER (PARTITION BY m.COMPANYCODE, m.ENDDATE
               ORDER BY m.INFOPUBLDATE DESC,
                        CASE m.MARK WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                            WHEN 6 THEN 4 WHEN 2 THEN 5 END,
                        m.JSID DESC, m.ID DESC) AS rn
    FROM lc_maindatanew m JOIN stock_base b ON b.COMPANYCODE = m.COMPANYCODE
    JOIN params p ON m.INFOPUBLDATE < p.cutoff AND m.ENDDATE <= p.as_of_date
    WHERE m.MARK IN (1, 8, 7, 6, 2) AND m.ACCOUNTINGSTANDARDS = 1
      AND (m.ENDDATE IN (DATE('2021-12-31'), DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
           OR m.ENDDATE >= DATE('2025-01-01'))
      AND DATE_FORMAT(m.ENDDATE, '%m-%d') IN ('03-31', '06-30', '09-30', '12-31')
),
financial AS (
    SELECT * FROM financial_ranked WHERE rn = 1
),
annual_metrics AS (
    SELECT m.COMPANYCODE,
           AVG(CAST(m.NETPROFIT AS DECIMAL(38,16)) / ((a.TOTALASSETS + m.TOTALASSETS) / 2)) AS avg_roa_3y,
           AVG(CAST(m.NETOPERATECASHFLOW AS DECIMAL(38,16)) / m.OPERATINGREENUE) AS avg_cfo_to_revenue_3y
    FROM financial m JOIN financial a
      ON a.COMPANYCODE = m.COMPANYCODE AND a.ENDDATE = DATE_SUB(m.ENDDATE, INTERVAL 1 YEAR)
    WHERE m.ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
      AND m.NETPROFIT IS NOT NULL AND m.NETOPERATECASHFLOW IS NOT NULL
      AND (a.TOTALASSETS + m.TOTALASSETS) / 2 > 0 AND m.OPERATINGREENUE > 0
    GROUP BY m.COMPANYCODE HAVING COUNT(*) = 3
),
latest_period AS (
    SELECT COMPANYCODE, MAX(ENDDATE) AS ENDDATE FROM financial GROUP BY COMPANYCODE
),
financial_metrics AS (
    SELECT a.COMPANYCODE, a.avg_roa_3y, a.avg_cfo_to_revenue_3y,
           CAST(m.SURPLUSRESERVEFUND + m.RETAINEDPROFIT AS DECIMAL(38,16)) / m.TOTALASSETS AS retained_earning_asset
    FROM annual_metrics a JOIN latest_period lp ON lp.COMPANYCODE = a.COMPANYCODE
    JOIN financial m ON m.COMPANYCODE = lp.COMPANYCODE AND m.ENDDATE = lp.ENDDATE
    WHERE m.SURPLUSRESERVEFUND IS NOT NULL AND m.RETAINEDPROFIT IS NOT NULL AND m.TOTALASSETS > 0
),
quality_base AS (
    SELECT b.*, f.avg_roa_3y, f.avg_cfo_to_revenue_3y, f.retained_earning_asset
    FROM stock_base b JOIN financial_metrics f ON f.COMPANYCODE = b.COMPANYCODE
    JOIN liquidity li ON li.INNERCODE = b.INNERCODE
),
quality_scored AS (
    SELECT qb.*,
           RANK() OVER (ORDER BY avg_roa_3y) + RANK() OVER (ORDER BY avg_cfo_to_revenue_3y)
               + RANK() OVER (ORDER BY retained_earning_asset) - 3 AS rank_sum,
           COUNT(*) OVER () AS sample_size
    FROM quality_base qb
),
final_pool AS (
    SELECT qs.*,
           -- Integer rank sums preserve equal mean percentiles exactly.
           CASE WHEN sample_size = 1 THEN 0
                ELSE CAST(rank_sum AS DECIMAL(38,16)) / (3 * (sample_size - 1)) END AS quality_score,
           PERCENT_RANK() OVER (ORDER BY rank_sum DESC) AS quality_rank
    FROM quality_scored qs
),
valuation_ranked AS (
    SELECT v.INNERCODE, v.NEGOTIABLEMV,
           ROW_NUMBER() OVER (PARTITION BY v.INNERCODE ORDER BY v.JSID DESC, v.ID DESC) AS rn
    FROM lc_dindicesforvaluation v JOIN params p ON v.TRADINGDAY = p.as_of_date
    JOIN stock_base b ON b.INNERCODE = v.INNERCODE
)
SELECT f.SECUCODE AS `股票代码`, f.stock_name AS `公司简称`,
       ROUND(f.quality_score, 4) AS `质量得分`,
       ROUND(v.NEGOTIABLEMV / 100000000, 4) AS `当日流通市值(亿元)`
FROM final_pool f JOIN valuation_ranked v ON v.INNERCODE = f.INNERCODE AND v.rn = 1
WHERE f.quality_rank <= 0.20 AND v.NEGOTIABLEMV > 0
ORDER BY v.NEGOTIABLEMV, f.SECUCODE LIMIT 20;
