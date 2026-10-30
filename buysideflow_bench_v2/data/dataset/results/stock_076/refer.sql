WITH
params AS (
    SELECT
        DATE('2023-01-01') AS start_date,
        DATE('2025-06-30') AS end_date
),
periodic_disclosures AS (
    SELECT m.COMPANYCODE, DATE(m.ENDDATE) AS report_period,
           MIN(DATE(m.INFOPUBLDATE)) AS first_publish_date
    FROM lc_maindatanew m
    WHERE m.INFOPUBLDATE < '2025-07-01' AND m.ENDDATE <= '2025-06-30'
      AND CAST(m.BULLETINTYPE AS UNSIGNED) = 20
      AND m.INFOSOURCECODE IN (110101, 110102, 110103, 110104)
    GROUP BY m.COMPANYCODE, DATE(m.ENDDATE)
),
financial_source AS (
    SELECT m.COMPANYCODE, m.ENDDATE, m.INFOPUBLDATE, m.ROEWEIGHTED,
           m.INSERTTIME, m.ID, m.JSID, 0 AS adjustment_priority
    FROM lc_mainindexnew m
    JOIN periodic_disclosures d ON d.COMPANYCODE = m.COMPANYCODE
      AND d.report_period = DATE(m.ENDDATE) AND DATE(m.INFOPUBLDATE) = d.first_publish_date
    WHERE m.INFOPUBLDATE < '2025-07-01'
      AND EXISTS (SELECT 1 FROM secumain s WHERE s.COMPANYCODE = m.COMPANYCODE
                  AND s.SECUCATEGORY = 1 AND s.SECUMARKET IN (83, 90) AND s.LISTEDSECTOR <> 7)
    UNION ALL
    SELECT m.COMPANYCODE, m.ENDDATE, m.INFOPUBLDATE, m.ROEWEIGHTED,
           m.INSERTTIME, m.ID, m.JSID,
           CASE WHEN m.IFADJUSTED = 1 THEN 0 ELSE 1 END
    FROM lc_stibmainindex m
    WHERE m.IFMERGED = 1 AND m.INFOPUBLDATE < '2025-07-01'
      AND m.INFOSOURCECODE IN (110101, 110102, 110103, 110104)
      AND EXISTS (SELECT 1 FROM secumain s WHERE s.COMPANYCODE = m.COMPANYCODE
                  AND s.SECUCATEGORY = 1 AND s.SECUMARKET IN (83, 90) AND s.LISTEDSECTOR = 7)
),
report_base AS (
    SELECT
        m.COMPANYCODE,
        s.INNERCODE,
        s.SECUCODE,
        s.LISTEDSECTOR,
        DATE(m.ENDDATE) AS report_period,
        DATE(m.INFOPUBLDATE) AS publish_date,
        m.ROEWEIGHTED AS roe_weighted,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
            ORDER BY
                DATE(m.INFOPUBLDATE) ASC,
                m.adjustment_priority,
                COALESCE(m.INSERTTIME, m.INFOPUBLDATE) ASC,
                m.ID ASC,
                m.JSID ASC
        ) AS rn
    FROM financial_source m
    JOIN secumain s
      ON s.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.INFOPUBLDATE < DATE_ADD(p.end_date, INTERVAL 1 DAY)
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND m.ENDDATE <= DATE('2025-06-30')
      AND m.INFOPUBLDATE IS NOT NULL
      AND DATE_FORMAT(m.ENDDATE, '%m-%d') IN ('03-31', '06-30', '09-30', '12-31')
),
reports AS (
    SELECT
        COMPANYCODE,
        INNERCODE,
        SECUCODE,
        LISTEDSECTOR,
        report_period,
        publish_date,
        roe_weighted
    FROM report_base
    WHERE rn = 1
      AND publish_date >= (SELECT start_date FROM params)
      AND roe_weighted BETWEEN -100 AND 100
),
trading_calendar AS (
    SELECT
        tradingday,
        ROW_NUMBER() OVER (ORDER BY tradingday) AS trade_seq
    FROM (
        SELECT DISTINCT t.TRADINGDATE AS tradingday
        FROM qt_tradingdaynew t
        JOIN params p
          ON t.TRADINGDATE BETWEEN p.start_date AND DATE_ADD(p.end_date, INTERVAL 120 DAY)
        WHERE t.IFTRADINGDAY = 1
          AND t.SECUMARKET IN (83, 90)
    ) d
),
report_start AS (
    SELECT
        r.COMPANYCODE,
        r.INNERCODE,
        r.SECUCODE,
        r.LISTEDSECTOR,
        r.report_period,
        r.publish_date,
        r.roe_weighted,
        MIN(tc.trade_seq) AS base_seq
    FROM reports r
    JOIN trading_calendar tc
      ON tc.tradingday > r.publish_date
    GROUP BY
        r.COMPANYCODE,
        r.INNERCODE,
        r.SECUCODE,
        r.LISTEDSECTOR,
        r.report_period,
        r.publish_date,
        r.roe_weighted
),
eligible AS (
    SELECT
        rs.COMPANYCODE,
        rs.INNERCODE,
        rs.SECUCODE,
        rs.LISTEDSECTOR,
        rs.report_period,
        rs.publish_date,
        rs.roe_weighted,
        base.tradingday AS base_date,
        future.tradingday AS future_date,
        p0.BACKWARDPRICE AS base_price,
        p20.BACKWARDPRICE AS future_price,
        p20.BACKWARDPRICE / NULLIF(p0.BACKWARDPRICE, 0) - 1 AS return_20d
    FROM report_start rs
    JOIN trading_calendar base
      ON base.trade_seq = rs.base_seq
    JOIN trading_calendar future
      ON future.trade_seq = rs.base_seq + 20
    JOIN qt_performancedata p0
      ON p0.INNERCODE = rs.INNERCODE
     AND p0.TRADINGDAY = base.tradingday
    LEFT JOIN qt_performancedata p20
      ON p20.INNERCODE = rs.INNERCODE
     AND p20.TRADINGDAY = future.tradingday
     AND p20.BACKWARDPRICE > 0
    WHERE p0.BACKWARDPRICE > 0
      AND NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = rs.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND DATE(ra.IMPLEMENTDATE) <= base.tradingday
            AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE)
                < DATE_ADD(base.tradingday, INTERVAL 1 DAY)
            AND (
                ra.REMOVEDATE IS NULL
                OR DATE(ra.REMOVEDATE) > base.tradingday
                OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE)
                    >= DATE_ADD(base.tradingday, INTERVAL 1 DAY)
            )
          )
),
industry_asof AS (
    SELECT
        t.COMPANYCODE,
        t.INNERCODE,
        t.report_period,
        t.industry_code,
        t.industry_name
    FROM (
        SELECT
            e.COMPANYCODE,
            el.INNERCODE,
            el.report_period,
            e.FIRSTINDUSTRYCODE AS industry_code,
            e.FIRSTINDUSTRYNAME AS industry_name,
            ROW_NUMBER() OVER (
                PARTITION BY el.COMPANYCODE, el.INNERCODE, el.report_period
                ORDER BY
                    e.INFOPUBLDATE DESC,
                    COALESCE(e.CANCELDATE, DATE('9999-12-31')) DESC,
                    COALESCE(e.INSERTTIME, e.INFOPUBLDATE) DESC,
                    e.ID DESC,
                    e.JSID DESC
            ) AS rn
        FROM eligible el
        JOIN lc_exgindustry e
          ON e.COMPANYCODE = el.COMPANYCODE
         AND el.LISTEDSECTOR <> 7
         AND e.STANDARD = 38
         AND e.INFOPUBLDATE <= el.base_date
         AND (e.CANCELDATE IS NULL OR e.CANCELDATE > el.base_date)
        UNION ALL
        SELECT
            e.COMPANYCODE,
            el.INNERCODE,
            el.report_period,
            e.FIRSTINDUSTRYCODE AS industry_code,
            e.FIRSTINDUSTRYNAME AS industry_name,
            ROW_NUMBER() OVER (
                PARTITION BY el.COMPANYCODE, el.INNERCODE, el.report_period
                ORDER BY
                    e.INFOPUBLDATE DESC,
                    COALESCE(e.CANCELDATE, DATE('9999-12-31')) DESC,
                    COALESCE(e.INSERTTIME, e.INFOPUBLDATE) DESC,
                    e.ID DESC,
                    e.JSID DESC
            ) AS rn
        FROM eligible el
        JOIN lc_stibexgindustry e
          ON e.COMPANYCODE = el.COMPANYCODE
         AND el.LISTEDSECTOR = 7
         AND e.STANDARD = 38
         AND e.EFFECTIVEDATE <= el.base_date
         AND e.INFOPUBLDATE <= el.base_date
         AND (e.CANCELDATE IS NULL OR e.CANCELDATE > el.base_date)
    ) t
    WHERE t.rn = 1
),
factor_base AS (
    SELECT
        el.report_period,
        el.COMPANYCODE,
        el.INNERCODE,
        el.SECUCODE,
        ia.industry_code,
        ia.industry_name,
        el.publish_date,
        el.base_date,
        el.future_date,
        el.roe_weighted,
        el.return_20d
    FROM eligible el
    JOIN industry_asof ia
      ON ia.COMPANYCODE = el.COMPANYCODE
     AND ia.INNERCODE = el.INNERCODE
     AND ia.report_period = el.report_period
),
factor_panel AS (
    SELECT
        fb.*,
        fb.roe_weighted - AVG(fb.roe_weighted) OVER (
            PARTITION BY fb.report_period, fb.industry_code
            ORDER BY fb.publish_date
            RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS roe_neutralized
    FROM factor_base fb
),
rank_base AS (
    SELECT
        fp.*,
        RANK() OVER (PARTITION BY report_period ORDER BY roe_weighted ASC) AS raw_rank_min,
        COUNT(*) OVER (PARTITION BY report_period, roe_weighted) AS raw_tie_count,
        RANK() OVER (PARTITION BY report_period ORDER BY roe_neutralized ASC) AS neutral_rank_min,
        COUNT(*) OVER (PARTITION BY report_period, roe_neutralized) AS neutral_tie_count,
        RANK() OVER (PARTITION BY report_period ORDER BY return_20d ASC) AS ret_rank_min,
        COUNT(*) OVER (PARTITION BY report_period, return_20d) AS ret_tie_count
    FROM factor_panel fp
    WHERE return_20d IS NOT NULL
),
ranked AS (
    SELECT
        report_period,
        industry_code,
        roe_weighted,
        roe_neutralized,
        return_20d,
        raw_rank_min + (raw_tie_count - 1) / 2.0 AS raw_rank,
        neutral_rank_min + (neutral_tie_count - 1) / 2.0 AS neutral_rank,
        ret_rank_min + (ret_tie_count - 1) / 2.0 AS ret_rank,
        COUNT(*) OVER (PARTITION BY report_period) AS sample_size
    FROM rank_base
),
period_ic AS (
    SELECT
        report_period,
        COUNT(*) AS sample_count,
        COUNT(DISTINCT industry_code) AS industry_count,
        (
            COUNT(*) * SUM(raw_rank * ret_rank) - SUM(raw_rank) * SUM(ret_rank)
        ) / NULLIF(
            SQRT(
                (COUNT(*) * SUM(raw_rank * raw_rank) - POW(SUM(raw_rank), 2))
                * (COUNT(*) * SUM(ret_rank * ret_rank) - POW(SUM(ret_rank), 2))
            ),
            0
        ) AS raw_ic,
        (
            COUNT(*) * SUM(neutral_rank * ret_rank) - SUM(neutral_rank) * SUM(ret_rank)
        ) / NULLIF(
            SQRT(
                (COUNT(*) * SUM(neutral_rank * neutral_rank) - POW(SUM(neutral_rank), 2))
                * (COUNT(*) * SUM(ret_rank * ret_rank) - POW(SUM(ret_rank), 2))
            ),
            0
        ) AS neutral_ic
    FROM ranked
    GROUP BY report_period
    HAVING COUNT(*) >= 30
       AND COUNT(DISTINCT industry_code) >= 3
)
SELECT
    COUNT(*) AS `有效报告期数`,
    ROUND(AVG(raw_ic), 6) AS `加权ROE平均IC`,
    ROUND(STDDEV_POP(raw_ic), 6) AS `加权ROE IC标准差`,
    ROUND(AVG(neutral_ic), 6) AS `行业中性化ROE平均IC`,
    ROUND(STDDEV_POP(neutral_ic), 6) AS `行业中性化ROE IC标准差`
FROM period_ic;
