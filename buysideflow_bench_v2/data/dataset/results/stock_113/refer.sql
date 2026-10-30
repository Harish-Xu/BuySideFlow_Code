WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date,
           DATE('2026-01-01') AS publish_cutoff
),
trade_day AS (
    SELECT MAX(TRADINGDATE) AS latest_trade_day
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET = 83
),
stock_candidates AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        s.SECUCODE,
        s.LISTEDSECTOR,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name
    FROM (
        SELECT
            s.*,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.INNERCODE
            ) AS rn
        FROM secumain s
        JOIN params p
          ON s.LISTEDDATE < p.publish_cutoff
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2, 6, 7, 8)
    ) s
    WHERE s.rn = 1
),
listing_asof AS (
    SELECT INNERCODE, CHANGETYPE
    FROM (
        SELECT l.INNERCODE, l.CHANGETYPE,
               ROW_NUMBER() OVER (
                   PARTITION BY l.INNERCODE
                   ORDER BY l.CHANGEDATE DESC, l.JSID DESC
               ) AS rn
        FROM (
            SELECT l.INNERCODE, l.CHANGEDATE, l.CHANGETYPE, l.JSID
            FROM lc_liststatus l
            JOIN stock_candidates s
              ON s.INNERCODE = l.INNERCODE AND s.LISTEDSECTOR <> 7
            UNION ALL
            SELECT l.INNERCODE, l.CHANGEDATE, l.CHANGETYPE, l.JSID
            FROM lc_stibliststatus l
            JOIN stock_candidates s
              ON s.INNERCODE = l.INNERCODE AND s.LISTEDSECTOR = 7
        ) l
        JOIN params p
          ON l.CHANGEDATE < p.publish_cutoff
        WHERE l.CHANGETYPE IN (1, 2, 3, 4, 5, 6)
    ) l
    WHERE rn = 1
),
stock_base AS (
    SELECT s.*
    FROM stock_candidates s
    LEFT JOIN listing_asof l ON l.INNERCODE = s.INNERCODE
    WHERE l.INNERCODE IS NULL OR l.CHANGETYPE IN (1, 2, 3, 6)
),
industry_asof AS (
    SELECT
        COMPANYCODE,
        FIRSTINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC,
                         e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR <> 7
        JOIN params p
          ON e.INFOPUBLDATE < p.publish_cutoff
        WHERE e.STANDARD = 37
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
        UNION ALL
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
            ) AS rn
        FROM lc_stibexgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR = 7
        JOIN params p
          ON e.INFOPUBLDATE < p.publish_cutoff
        WHERE e.STANDARD = 37
          AND e.EFFECTIVEDATE <= p.as_of_date
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    ) t
    WHERE rn = 1
      AND FIRSTINDUSTRYNAME IS NOT NULL
),
valuation_sources AS (
    SELECT v.INNERCODE, v.NEGOTIABLEMV, v.JSID, v.ID
    FROM lc_dindicesforvaluation v
    JOIN stock_base s
      ON s.INNERCODE = v.INNERCODE AND s.LISTEDSECTOR <> 7
    JOIN trade_day td ON v.TRADINGDAY = td.latest_trade_day
    WHERE v.NEGOTIABLEMV > 0
    UNION ALL
    SELECT v.INNERCODE, v.NEGOTIABLEMV, v.JSID, v.ID
    FROM lc_stibdindiforvalue v
    JOIN stock_base s
      ON s.INNERCODE = v.INNERCODE AND s.LISTEDSECTOR = 7
    JOIN trade_day td ON v.TRADINGDAY = td.latest_trade_day
    WHERE v.NEGOTIABLEMV > 0
),
valuation_asof AS (
    SELECT INNERCODE, NEGOTIABLEMV
    FROM (
        SELECT
            v.INNERCODE,
            v.NEGOTIABLEMV,
            ROW_NUMBER() OVER (
                PARTITION BY v.INNERCODE
                ORDER BY v.JSID DESC, v.ID DESC
            ) AS rn
        FROM valuation_sources v
    ) t
    WHERE rn = 1
),
latest_val AS (
    SELECT
        sb.COMPANYCODE,
        sb.SECUCODE,
        sb.LISTEDSECTOR,
        sb.stock_name,
        ia.FIRSTINDUSTRYNAME AS industry_name,
        v.NEGOTIABLEMV,
        ROW_NUMBER() OVER (
            PARTITION BY ia.FIRSTINDUSTRYNAME
            ORDER BY v.NEGOTIABLEMV DESC, sb.SECUCODE
        ) AS industry_mv_rank
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    JOIN valuation_asof v
      ON v.INNERCODE = sb.INNERCODE
),
industry_leaders AS (
    SELECT *
    FROM latest_val
    WHERE industry_mv_rank <= 5
),
report_rows AS (
    SELECT m.COMPANYCODE, m.ENDDATE, m.INFOPUBLDATE, m.ROE,
           m.JSID, m.ID, m.MARK AS adjustment_flag
    FROM lc_maindatanew m
    JOIN industry_leaders sb ON sb.COMPANYCODE = m.COMPANYCODE AND sb.LISTEDSECTOR <> 7
    JOIN params p ON m.INFOPUBLDATE < p.publish_cutoff AND m.ENDDATE <= p.as_of_date
    WHERE CAST(m.BULLETINTYPE AS UNSIGNED) = 20
      AND m.INFOSOURCECODE IN (110101, 110102, 110103, 110104)
      AND m.ACCOUNTINGSTANDARDS = 1 AND m.MARK IN (1, 8, 7, 6, 2)
    UNION ALL
    SELECT m.COMPANYCODE, m.ENDDATE, m.INFOPUBLDATE, m.ROE,
           m.JSID, m.ID, m.IFADJUSTED AS adjustment_flag
    FROM lc_stibmaindata m
    JOIN industry_leaders sb ON sb.COMPANYCODE = m.COMPANYCODE AND sb.LISTEDSECTOR = 7
    JOIN params p ON m.INFOPUBLDATE < p.publish_cutoff AND m.ENDDATE <= p.as_of_date
    WHERE m.INFOSOURCECODE IN (110101, 110102, 110103, 110104)
      AND m.IFMERGED = 1 AND m.IFADJUSTED IN (1, 8, 7, 6, 2)
),
indicator_rows AS (
    SELECT m.COMPANYCODE, m.ENDDATE, m.INFOPUBLDATE, m.ROE,
           m.JSID, m.ID, 0 AS adjustment_priority
    FROM lc_mainindexnew m
    JOIN industry_leaders sb ON sb.COMPANYCODE = m.COMPANYCODE AND sb.LISTEDSECTOR <> 7
    JOIN params p ON m.INFOPUBLDATE < p.publish_cutoff AND m.ENDDATE <= p.as_of_date
    UNION ALL
    SELECT m.COMPANYCODE, m.ENDDATE, m.INFOPUBLDATE, m.ROE,
           m.JSID, m.ID, CASE WHEN m.IFADJUSTED = 1 THEN 0 ELSE 1 END
    FROM lc_stibmainindex m
    JOIN industry_leaders sb ON sb.COMPANYCODE = m.COMPANYCODE AND sb.LISTEDSECTOR = 7
    JOIN params p ON m.INFOPUBLDATE < p.publish_cutoff AND m.ENDDATE <= p.as_of_date
    WHERE m.IFMERGED = 1
),
report_period_sources AS (
    SELECT m.COMPANYCODE, m.ENDDATE
    FROM lc_maindatanew m
    JOIN industry_leaders sb ON sb.COMPANYCODE = m.COMPANYCODE AND sb.LISTEDSECTOR <> 7
    JOIN params p ON m.INFOPUBLDATE < p.publish_cutoff AND m.ENDDATE <= p.as_of_date
    WHERE CAST(m.BULLETINTYPE AS UNSIGNED) = 20
      AND m.INFOSOURCECODE IN (110101, 110102, 110103, 110104)
    UNION ALL
    SELECT m.COMPANYCODE, m.ENDDATE
    FROM lc_stibmaindata m
    JOIN industry_leaders sb ON sb.COMPANYCODE = m.COMPANYCODE AND sb.LISTEDSECTOR = 7
    JOIN params p ON m.INFOPUBLDATE < p.publish_cutoff AND m.ENDDATE <= p.as_of_date
    WHERE m.INFOSOURCECODE IN (110101, 110102, 110103, 110104)
),
latest_report_period AS (
    SELECT COMPANYCODE, MAX(ENDDATE) AS report_period
    FROM (
        SELECT COMPANYCODE, ENDDATE FROM report_period_sources
        UNION ALL
        SELECT COMPANYCODE, ENDDATE FROM indicator_rows
    ) periods
    GROUP BY COMPANYCODE
),
roe_sources AS (
    SELECT m.COMPANYCODE, m.ENDDATE, m.INFOPUBLDATE, m.ROE,
           m.JSID, m.ID, m.adjustment_priority, 0 AS source_priority
    FROM indicator_rows m
    UNION ALL
    SELECT m.COMPANYCODE, m.ENDDATE, m.INFOPUBLDATE, m.ROE,
           m.JSID, m.ID,
           CASE m.adjustment_flag WHEN 1 THEN 0 WHEN 8 THEN 1 WHEN 7 THEN 2
                WHEN 6 THEN 3 WHEN 2 THEN 4 END, 1 AS source_priority
    FROM report_rows m
),
latest_roe AS (
    SELECT COMPANYCODE, ROE
    FROM (
        SELECT m.*, ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE
            ORDER BY m.source_priority,
                     CASE WHEN m.source_priority = 1 THEN m.adjustment_priority ELSE 0 END,
                     m.INFOPUBLDATE DESC,
                     m.adjustment_priority, m.JSID DESC, m.ID DESC
        ) AS rn
        FROM roe_sources m
        JOIN latest_report_period rp ON rp.COMPANYCODE = m.COMPANYCODE
          AND rp.report_period = m.ENDDATE
    ) ranked
    WHERE rn = 1 AND ROE IS NOT NULL
)
SELECT
    lv.SECUCODE AS `股票代码`,
    lv.stock_name AS `股票简称`,
    ROUND(lr.ROE, 4) AS `ROE`,
    ROUND(lv.NEGOTIABLEMV / 100000000, 2) AS `流通市值`
FROM industry_leaders lv
JOIN latest_roe lr ON lr.COMPANYCODE = lv.COMPANYCODE
ORDER BY lr.ROE DESC, lv.SECUCODE
LIMIT 10;
