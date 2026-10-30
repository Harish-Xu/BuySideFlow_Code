WITH
params AS (
    SELECT
        DATE('2024-01-01') AS start_date,
        DATE('2024-12-31') AS end_date,
        DATE_ADD(DATE('2024-12-31'), INTERVAL 120 DAY) AS future_end_date
),
target_index AS (
    SELECT s.INNERCODE
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE = '399997'
    ORDER BY s.INNERCODE
    LIMIT 1
),
trading_calendar AS (
    SELECT
        tradingday,
        ROW_NUMBER() OVER (ORDER BY tradingday) AS trade_seq
    FROM (
        SELECT DISTINCT t.TRADINGDATE AS tradingday
        FROM qt_tradingdaynew t
        JOIN params p
          ON t.TRADINGDATE BETWEEN p.start_date AND p.future_end_date
        WHERE t.IFTRADINGDAY = 1
          AND t.SECUMARKET IN (83, 90)
    ) d
),
first_visible_roe AS (
    SELECT
        COMPANYCODE,
        report_period,
        publish_date,
        roe_weighted
    FROM (
        SELECT
            m.COMPANYCODE,
            DATE(m.ENDDATE) AS report_period,
            DATE(m.INFOPUBLDATE) AS publish_date,
            m.ROEWEIGHTED AS roe_weighted,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
                ORDER BY
                    DATE(m.INFOPUBLDATE) ASC,
                    COALESCE(m.INSERTTIME, m.INFOPUBLDATE) ASC,
                    m.ID ASC,
                    m.JSID ASC
            ) AS rn
        FROM lc_mainindexnew m
        JOIN params p
          ON m.INFOPUBLDATE >= p.start_date
         AND m.INFOPUBLDATE < DATE_ADD(p.end_date, INTERVAL 1 DAY)
        WHERE m.INFOPUBLDATE IS NOT NULL
          AND DATE_FORMAT(m.ENDDATE, '%m-%d') IN ('03-31', '06-30', '09-30', '12-31')
    ) x
    WHERE rn = 1
      AND roe_weighted IS NOT NULL
),
report_start AS (
    SELECT
        r.COMPANYCODE,
        r.report_period,
        r.publish_date,
        r.roe_weighted,
        MIN(tc.trade_seq) AS base_seq
    FROM first_visible_roe r
    JOIN trading_calendar tc
      ON tc.tradingday > r.publish_date
    GROUP BY
        r.COMPANYCODE,
        r.report_period,
        r.publish_date,
        r.roe_weighted
),
report_calendar AS (
    SELECT
        rs.COMPANYCODE,
        rs.report_period,
        rs.publish_date,
        rs.roe_weighted,
        base.tradingday AS base_date,
        d5.tradingday AS date_5d,
        d10.tradingday AS date_10d,
        d20.tradingday AS date_20d
    FROM report_start rs
    JOIN trading_calendar base
      ON base.trade_seq = rs.base_seq
    JOIN trading_calendar d5
      ON d5.trade_seq = rs.base_seq + 5
    JOIN trading_calendar d10
      ON d10.trade_seq = rs.base_seq + 10
    JOIN trading_calendar d20
      ON d20.trade_seq = rs.base_seq + 20
),
index_members AS (
    SELECT
        rc.COMPANYCODE,
        rc.report_period,
        rc.publish_date,
        rc.roe_weighted,
        rc.base_date,
        rc.date_5d,
        rc.date_10d,
        rc.date_20d,
        sm.INNERCODE,
        sm.SECUCODE
    FROM report_calendar rc
    JOIN target_index ti
    JOIN lc_indexcomponent ic
      ON ic.INDEXINNERCODE = ti.INNERCODE
     AND ic.INDATE <= rc.base_date
     AND (ic.OUTDATE IS NULL OR ic.OUTDATE > rc.base_date)
    JOIN secumain sm
      ON sm.INNERCODE = ic.SECUINNERCODE
     AND sm.COMPANYCODE = rc.COMPANYCODE
    WHERE sm.SECUCATEGORY = 1
      AND sm.SECUMARKET IN (83, 90)
),
base_tradable AS (
    SELECT
        im.*,
        p0.BACKWARDPRICE AS price_0d
    FROM index_members im
    JOIN qt_performancedata p0
      ON p0.INNERCODE = im.INNERCODE
     AND p0.TRADINGDAY = im.base_date
    WHERE p0.BACKWARDPRICE > 0
),
roe_quintiles AS (
    SELECT
        bt.*,
        NTILE(5) OVER (
            PARTITION BY bt.report_period
            ORDER BY bt.roe_weighted ASC, bt.SECUCODE ASC
        ) AS quintile_group
    FROM base_tradable bt
),
return_samples AS (
    SELECT
        rq.report_period,
        rq.COMPANYCODE,
        rq.SECUCODE,
        rq.roe_weighted,
        rq.quintile_group,
        p5.BACKWARDPRICE / NULLIF(rq.price_0d, 0) - 1 AS return_5d,
        p10.BACKWARDPRICE / NULLIF(rq.price_0d, 0) - 1 AS return_10d,
        p20.BACKWARDPRICE / NULLIF(rq.price_0d, 0) - 1 AS return_20d
    FROM roe_quintiles rq
    JOIN qt_performancedata p5
      ON p5.INNERCODE = rq.INNERCODE
     AND p5.TRADINGDAY = rq.date_5d
    JOIN qt_performancedata p10
      ON p10.INNERCODE = rq.INNERCODE
     AND p10.TRADINGDAY = rq.date_10d
    JOIN qt_performancedata p20
      ON p20.INNERCODE = rq.INNERCODE
     AND p20.TRADINGDAY = rq.date_20d
    WHERE p5.BACKWARDPRICE > 0
      AND p10.BACKWARDPRICE > 0
      AND p20.BACKWARDPRICE > 0
)
SELECT
    CASE quintile_group
        WHEN 1 THEN 'Q1_ROE最低'
        WHEN 2 THEN 'Q2_ROE较低'
        WHEN 3 THEN 'Q3_ROE中等'
        WHEN 4 THEN 'Q4_ROE较高'
        WHEN 5 THEN 'Q5_ROE最高'
    END AS `分组`,
    COUNT(*) AS `样本量`,
    COUNT(DISTINCT report_period) AS `报告期数`,
    ROUND(AVG(roe_weighted), 2) AS `平均ROE(%)`,
    ROUND(AVG(return_5d) * 100, 2) AS `5日平均收益率(%)`,
    ROUND(AVG(return_10d) * 100, 2) AS `10日平均收益率(%)`,
    ROUND(AVG(return_20d) * 100, 2) AS `20日平均收益率(%)`
FROM return_samples
GROUP BY quintile_group
ORDER BY quintile_group;
