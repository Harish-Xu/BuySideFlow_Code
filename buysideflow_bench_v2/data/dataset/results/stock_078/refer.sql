-- Audit-only single-query sketch. The executable implementation is refer.py,
-- which stages the same logic to avoid very large correlated joins.
WITH
params AS (
	    SELECT
	        DATE('2025-01-01') AS disclosure_start_date,
	        DATE('2026-01-12') AS observe_date,
	        DATE('2024-12-31') AS min_report_period
),
stock_base AS (
    SELECT
        x.COMPANYCODE,
        x.INNERCODE,
        x.SECUCODE,
        x.LISTEDSECTOR
    FROM (
        SELECT
            s.COMPANYCODE,
            s.INNERCODE,
            s.SECUCODE,
            s.LISTEDSECTOR,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY
	                    s.SECUMARKET,
	                    s.SECUCODE,
	                    s.INNERCODE
	            ) AS rn
	        FROM secumain s
		        WHERE s.SECUMARKET IN (83, 90)
          AND s.SECUCATEGORY = 1
          AND s.LISTEDSECTOR = 1
          AND s.COMPANYCODE IS NOT NULL
          AND s.INNERCODE IS NOT NULL
          AND s.SECUCODE IS NOT NULL
    ) x
    WHERE x.rn = 1
),
periodic_report_source AS (
    SELECT
        z.COMPANYCODE,
        z.report_period,
        z.publish_date,
        z.INFOSOURCECODE,
        z.INFOSOURCE,
        z.BULLETINTYPE
    FROM (
        SELECT
            d.COMPANYCODE,
            DATE(d.ENDDATE) AS report_period,
            DATE(d.INFOPUBLDATE) AS publish_date,
            d.INFOSOURCECODE,
            d.INFOSOURCE,
            d.BULLETINTYPE,
            ROW_NUMBER() OVER (
                PARTITION BY d.COMPANYCODE, DATE(d.ENDDATE)
                ORDER BY
                    d.INFOPUBLDATE,
                    COALESCE(d.INSERTTIME, d.INFOPUBLDATE),
                    d.ID,
                    d.JSID
            ) AS rn
        FROM lc_maindatanew d
        JOIN stock_base sb
          ON sb.COMPANYCODE = d.COMPANYCODE
        JOIN params p
          ON d.ENDDATE >= p.min_report_period
         AND d.ENDDATE < DATE_ADD(p.observe_date, INTERVAL 1 DAY)
         AND d.INFOPUBLDATE >= p.disclosure_start_date
         AND d.INFOPUBLDATE < DATE_ADD(p.observe_date, INTERVAL 1 DAY)
        WHERE d.INFOPUBLDATE IS NOT NULL
          AND d.ENDDATE IS NOT NULL
          AND CAST(d.BULLETINTYPE AS UNSIGNED) = 20
          AND d.INFOSOURCECODE IN (110101, 110102, 110103, 110104)
          AND DATE_FORMAT(d.ENDDATE, '%m-%d') IN ('03-31', '06-30', '09-30', '12-31')
    ) z
    WHERE z.rn = 1
),
first_report_version AS (
    SELECT
        y.COMPANYCODE,
        y.report_period,
        y.publish_date,
        y.revenue_growth_rate
    FROM (
        SELECT
            m.COMPANYCODE,
            DATE(m.ENDDATE) AS report_period,
            src.publish_date,
            m.OPERATINGREVENUEGROWRATE AS revenue_growth_rate,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
                ORDER BY
                    DATE(m.INFOPUBLDATE),
                    COALESCE(m.INSERTTIME, m.INFOPUBLDATE),
                    m.ID,
                    m.JSID
            ) AS rn
        FROM lc_mainindexnew m
        JOIN stock_base sb
          ON sb.COMPANYCODE = m.COMPANYCODE
        JOIN periodic_report_source src
          ON src.COMPANYCODE = m.COMPANYCODE
         AND src.report_period = DATE(m.ENDDATE)
         AND DATE(m.INFOPUBLDATE) = src.publish_date
        JOIN params p
         ON m.ENDDATE >= p.min_report_period
         AND m.ENDDATE < DATE_ADD(p.observe_date, INTERVAL 1 DAY)
         AND m.INFOPUBLDATE < DATE_ADD(p.observe_date, INTERVAL 1 DAY)
        WHERE m.INFOPUBLDATE IS NOT NULL
          AND m.ENDDATE IS NOT NULL
          AND m.OPERATINGREVENUEGROWRATE IS NOT NULL
          AND DATE_FORMAT(m.ENDDATE, '%m-%d') IN ('03-31', '06-30', '09-30', '12-31')
    ) y
    JOIN params p
      ON y.publish_date >= p.disclosure_start_date
     AND y.publish_date <= p.observe_date
    WHERE y.rn = 1
),
trading_calendar AS (
    SELECT
        d.trading_day,
        ROW_NUMBER() OVER (ORDER BY d.trading_day) AS trade_seq
    FROM (
        SELECT DISTINCT DATE(t.TRADINGDATE) AS trading_day
        FROM qt_tradingdaynew t
        JOIN params p
          ON t.TRADINGDATE >= p.disclosure_start_date
         AND t.TRADINGDATE < DATE_ADD(p.observe_date, INTERVAL 1 DAY)
        WHERE t.IFTRADINGDAY = 1
		          AND t.SECUMARKET IN (83, 90)
    ) d
),
event_start AS (
    SELECT
        r.COMPANYCODE,
        sb.INNERCODE,
        sb.SECUCODE,
        sb.LISTEDSECTOR,
        r.report_period,
        r.publish_date,
        r.revenue_growth_rate,
        MIN(tc.trade_seq) AS base_seq
    FROM first_report_version r
    JOIN stock_base sb
      ON sb.COMPANYCODE = r.COMPANYCODE
    JOIN trading_calendar tc
      ON tc.trading_day > r.publish_date
    GROUP BY
        r.COMPANYCODE,
        sb.INNERCODE,
        sb.SECUCODE,
        sb.LISTEDSECTOR,
        r.report_period,
        r.publish_date,
        r.revenue_growth_rate
),
event_return AS (
    SELECT
        es.COMPANYCODE,
        es.INNERCODE,
        es.SECUCODE,
        es.LISTEDSECTOR,
        es.report_period,
        es.publish_date,
        es.revenue_growth_rate,
        base.trading_day AS base_date,
        future.trading_day AS future_date,
        p0.BACKWARDPRICE AS base_backward_price,
        p20.BACKWARDPRICE AS future_backward_price,
        p20.BACKWARDPRICE / NULLIF(p0.BACKWARDPRICE, 0) - 1 AS return_20d
    FROM event_start es
    JOIN trading_calendar base
      ON base.trade_seq = es.base_seq
    JOIN trading_calendar future
      ON future.trade_seq = es.base_seq + 20
    JOIN qt_performancedata p0
      ON p0.INNERCODE = es.INNERCODE
     AND p0.TRADINGDAY = base.trading_day
    JOIN qt_performancedata p20
      ON p20.INNERCODE = es.INNERCODE
     AND p20.TRADINGDAY = future.trading_day
    WHERE p0.BACKWARDPRICE > 0
      AND p20.BACKWARDPRICE > 0
      AND (
          SELECT ls.CHANGETYPE
          FROM lc_liststatus ls
          WHERE ls.INNERCODE = es.INNERCODE
            AND ls.SECUMARKET IN (83, 90)
            AND ls.CHANGETYPE BETWEEN 1 AND 9
            AND ls.CHANGEDATE < DATE_ADD(base.trading_day, INTERVAL 1 DAY)
            AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME < DATE_ADD(base.trading_day, INTERVAL 1 DAY))
          ORDER BY
            ls.CHANGEDATE DESC,
            COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
            ls.JSID DESC,
            ls.ID DESC
          LIMIT 1
      ) IN (1, 3)
      AND NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = es.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND COALESCE(ra.IMPLANNOUCEDATE, ra.IMPLEMENTDATE) < DATE_ADD(base.trading_day, INTERVAL 1 DAY)
            AND ra.IMPLEMENTDATE < DATE_ADD(base.trading_day, INTERVAL 1 DAY)
            AND (
                ra.REMOVEDATE IS NULL
                OR ra.REMOVEDATE >= DATE_ADD(base.trading_day, INTERVAL 1 DAY)
                OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.REMOVEDATE) >= DATE_ADD(base.trading_day, INTERVAL 1 DAY)
            )
      )
)
SELECT
    COMPANYCODE,
    INNERCODE,
    SECUCODE,
    LISTEDSECTOR,
    report_period,
    publish_date,
    base_date,
    future_date,
    revenue_growth_rate,
    base_backward_price,
    future_backward_price,
    return_20d
FROM event_return
ORDER BY report_period, revenue_growth_rate, SECUCODE;
