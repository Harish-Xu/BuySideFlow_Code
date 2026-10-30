WITH
params AS (
    SELECT
        DATE('2024-06-30') AS as_of_date,
        TIMESTAMP('2024-07-01 00:00:00') AS as_of_dt
),
stock_base AS (
    SELECT INNERCODE, COMPANYCODE, SECUCODE, LISTEDSECTOR
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUCODE,
            s.LISTEDSECTOR,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.SECUCODE, s.INNERCODE
            ) AS rn
        FROM secumain s
        JOIN params p
          ON s.LISTEDDATE <= p.as_of_date
        WHERE s.SECUMARKET IN (83, 90)
          AND s.SECUCATEGORY = 1
    ) t
    WHERE rn = 1
),
periodic_rows AS (
    SELECT m.COMPANYCODE, m.ENDDATE, m.INFOPUBLDATE, m.ROE,
           m.INSERTTIME, m.ID, m.JSID, m.MARK AS adjustment_flag
    FROM lc_maindatanew m
    JOIN stock_base sb ON sb.COMPANYCODE = m.COMPANYCODE AND sb.LISTEDSECTOR <> 7
    JOIN params p ON m.INFOPUBLDATE < p.as_of_dt
    WHERE m.ENDDATE BETWEEN '2023-03-31' AND '2024-03-31'
      AND CAST(m.BULLETINTYPE AS UNSIGNED) = 20
      AND m.INFOSOURCECODE = CASE MONTH(m.ENDDATE)
          WHEN 3 THEN 110103 WHEN 6 THEN 110102
          WHEN 9 THEN 110104 WHEN 12 THEN 110101 END
      AND m.ACCOUNTINGSTANDARDS = 1 AND m.MARK IN (1, 8, 7, 6, 2)
    UNION ALL
    SELECT m.COMPANYCODE, m.ENDDATE, m.INFOPUBLDATE, m.ROE,
           m.INSERTTIME, m.ID, m.JSID, m.IFADJUSTED AS adjustment_flag
    FROM lc_stibmaindata m
    JOIN stock_base sb ON sb.COMPANYCODE = m.COMPANYCODE AND sb.LISTEDSECTOR = 7
    JOIN params p ON m.INFOPUBLDATE < p.as_of_dt
    WHERE m.ENDDATE BETWEEN '2023-03-31' AND '2024-03-31'
      AND m.INFOSOURCECODE = CASE MONTH(m.ENDDATE)
          WHEN 3 THEN 110103 WHEN 6 THEN 110102
          WHEN 9 THEN 110104 WHEN 12 THEN 110101 END
      AND m.IFMERGED = 1 AND m.IFADJUSTED IN (1, 8, 7, 6, 2)
),
periodic_disclosures AS (
    SELECT COMPANYCODE, DATE(ENDDATE) AS report_period,
           MIN(INFOPUBLDATE) AS first_publish_time
    FROM (
        SELECT m.COMPANYCODE, m.ENDDATE, m.INFOPUBLDATE
        FROM lc_maindatanew m
        JOIN stock_base sb ON sb.COMPANYCODE = m.COMPANYCODE AND sb.LISTEDSECTOR <> 7
        JOIN params p ON m.INFOPUBLDATE < p.as_of_dt
        WHERE m.ENDDATE BETWEEN '2023-03-31' AND '2024-03-31'
          AND CAST(m.BULLETINTYPE AS UNSIGNED) = 20
          AND m.INFOSOURCECODE = CASE MONTH(m.ENDDATE)
              WHEN 3 THEN 110103 WHEN 6 THEN 110102
              WHEN 9 THEN 110104 WHEN 12 THEN 110101 END
        UNION ALL
        SELECT m.COMPANYCODE, m.ENDDATE, m.INFOPUBLDATE
        FROM lc_stibmaindata m
        JOIN stock_base sb ON sb.COMPANYCODE = m.COMPANYCODE AND sb.LISTEDSECTOR = 7
        JOIN params p ON m.INFOPUBLDATE < p.as_of_dt
        WHERE m.ENDDATE BETWEEN '2023-03-31' AND '2024-03-31'
          AND m.INFOSOURCECODE = CASE MONTH(m.ENDDATE)
              WHEN 3 THEN 110103 WHEN 6 THEN 110102
              WHEN 9 THEN 110104 WHEN 12 THEN 110101 END
    ) disclosures
    GROUP BY COMPANYCODE, DATE(ENDDATE)
),
financial_source AS (
    SELECT m.*,
           CASE adjustment_flag WHEN 1 THEN 0 WHEN 8 THEN 1 WHEN 7 THEN 2
                WHEN 6 THEN 3 WHEN 2 THEN 4 END AS adjustment_priority
    FROM periodic_rows m
    JOIN periodic_disclosures d ON d.COMPANYCODE = m.COMPANYCODE
      AND d.report_period = DATE(m.ENDDATE) AND m.INFOPUBLDATE = d.first_publish_time
),
report_ranked AS (
    SELECT
        sb.INNERCODE,
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS report_period,
        DATE(m.INFOPUBLDATE) AS publish_date,
        m.ROE,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE ASC,
                     m.adjustment_priority,
                     COALESCE(m.INSERTTIME, m.INFOPUBLDATE) ASC,
                     m.ID ASC,
                     m.JSID ASC
        ) AS rn
    FROM financial_source m
    JOIN stock_base sb
      ON sb.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.INFOPUBLDATE < p.as_of_dt
    WHERE DATE(m.ENDDATE) IN (
        DATE('2023-03-31'), DATE('2023-06-30'), DATE('2023-09-30'),
        DATE('2023-12-31'), DATE('2024-03-31')
    )
),
reports AS (
    SELECT INNERCODE, COMPANYCODE, report_period, publish_date, ROE
    FROM report_ranked
    WHERE rn = 1
      AND ROE IS NOT NULL
),
trading_calendar AS (
    SELECT
        trading_day,
        ROW_NUMBER() OVER (ORDER BY trading_day) AS trade_seq
    FROM (
        SELECT DISTINCT DATE(t.TRADINGDATE) AS trading_day
        FROM qt_tradingdaynew t
        JOIN params p
          ON t.TRADINGDATE <= DATE_ADD(p.as_of_date, INTERVAL 120 DAY)
        WHERE t.TRADINGDATE >= DATE('2023-01-01')
          AND t.IFTRADINGDAY = 1
          AND t.SECUMARKET IN (83, 90)
    ) d
),
report_start AS (
    SELECT
        r.INNERCODE,
        r.COMPANYCODE,
        r.report_period,
        r.publish_date,
        r.ROE,
        MIN(tc.trade_seq) AS base_seq
    FROM reports r
    JOIN trading_calendar tc
      ON tc.trading_day > r.publish_date
    GROUP BY r.INNERCODE, r.COMPANYCODE, r.report_period, r.publish_date, r.ROE
),
event_dates AS (
    SELECT
        rs.*,
        d0.trading_day AS base_date,
        d20.trading_day AS date_20d
    FROM report_start rs
    JOIN trading_calendar d0 ON d0.trade_seq = rs.base_seq
    JOIN trading_calendar d20 ON d20.trade_seq = rs.base_seq + 20
),
holding_returns AS (
    SELECT
        ed.COMPANYCODE,
        ed.report_period,
        ed.ROE,
        p20.BACKWARDPRICE / NULLIF(p0.BACKWARDPRICE, 0) - 1 AS return_20d
    FROM event_dates ed
    JOIN qt_performancedata p0
      ON p0.INNERCODE = ed.INNERCODE
     AND p0.TRADINGDAY = ed.base_date
     AND p0.BACKWARDPRICE > 0
    JOIN qt_performancedata p20
      ON p20.INNERCODE = ed.INNERCODE
     AND p20.TRADINGDAY = ed.date_20d
     AND p20.BACKWARDPRICE > 0
    WHERE NOT EXISTS (
        SELECT 1
        FROM cs_riskalert ra
        WHERE ra.INNERCODE = ed.INNERCODE
          AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
          AND DATE(ra.IMPLEMENTDATE) <= ed.base_date
          AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE)
              < DATE_ADD(ed.base_date, INTERVAL 1 DAY)
          AND (
              ra.REMOVEDATE IS NULL
              OR DATE(ra.REMOVEDATE) > ed.base_date
              OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE)
                  >= DATE_ADD(ed.base_date, INTERVAL 1 DAY)
          )
    )
),
rank_base AS (
    SELECT h.*,
        RANK() OVER (PARTITION BY report_period ORDER BY ROE) AS roe_rank_min,
        COUNT(*) OVER (PARTITION BY report_period, ROE) AS roe_ties,
        RANK() OVER (PARTITION BY report_period ORDER BY return_20d) AS r20_rank_min,
        COUNT(*) OVER (PARTITION BY report_period, return_20d) AS r20_ties
    FROM holding_returns h
),
ranked AS (
    SELECT report_period,
        roe_rank_min + (roe_ties - 1) / 2.0 AS roe_rank,
        r20_rank_min + (r20_ties - 1) / 2.0 AS r20_rank
    FROM rank_base
),
period_ic AS (
    SELECT report_period, COUNT(*) AS sample_count,
        (COUNT(*) * SUM(roe_rank * r20_rank) - SUM(roe_rank) * SUM(r20_rank)) /
            NULLIF(SQRT(
                (COUNT(*) * SUM(roe_rank * roe_rank) - POW(SUM(roe_rank), 2)) *
                (COUNT(*) * SUM(r20_rank * r20_rank) - POW(SUM(r20_rank), 2))
            ), 0) AS ic_20d
    FROM ranked
    GROUP BY report_period
)
SELECT report_period AS `分析报告期`, sample_count AS `样本数`,
       ROUND(ic_20d, 4) AS `20日IC`
FROM period_ic
ORDER BY report_period;
