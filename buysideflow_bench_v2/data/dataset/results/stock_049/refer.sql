WITH
target_index AS (
    SELECT INNERCODE FROM secumain
    WHERE SECUCATEGORY = 4 AND SECUCODE = '000905'
    ORDER BY INNERCODE LIMIT 1
),
constituents AS (
    SELECT DISTINCT s.INNERCODE, s.COMPANYCODE, s.SECUCODE, s.SECUABBR, s.LISTEDSECTOR
    FROM lc_indexcomponent i
    JOIN target_index t ON t.INNERCODE = i.INDEXINNERCODE
    JOIN secumain s ON s.INNERCODE = i.SECUINNERCODE
    WHERE i.INDATE <= '2025-12-15' AND (i.OUTDATE IS NULL OR i.OUTDATE > '2025-12-15')
),
industry_rows AS (
    SELECT e.COMPANYCODE, e.FIRSTINDUSTRYNAME, e.INFOPUBLDATE, e.JSID, e.ID
    FROM lc_exgindustry e JOIN constituents s ON s.COMPANYCODE = e.COMPANYCODE
    WHERE s.LISTEDSECTOR <> 7 AND e.STANDARD = 38 AND e.INFOPUBLDATE < '2025-12-16'
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > '2025-12-15')
    UNION ALL
    SELECT e.COMPANYCODE, e.FIRSTINDUSTRYNAME, e.INFOPUBLDATE, e.JSID, e.ID
    FROM lc_stibexgindustry e JOIN constituents s ON s.COMPANYCODE = e.COMPANYCODE
    WHERE s.LISTEDSECTOR = 7 AND e.STANDARD = 38 AND e.INFOPUBLDATE < '2025-12-16'
      AND e.EFFECTIVEDATE <= '2025-12-15'
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > '2025-12-15')
),
industry_versions AS (
    SELECT e.*, ROW_NUMBER() OVER (
        PARTITION BY COMPANYCODE ORDER BY INFOPUBLDATE DESC, JSID DESC, ID DESC
    ) AS rn FROM industry_rows e
),
stock_pool AS (
    SELECT s.*, e.FIRSTINDUSTRYNAME FROM constituents s
    LEFT JOIN industry_versions e ON e.COMPANYCODE = s.COMPANYCODE AND e.rn = 1
    WHERE e.FIRSTINDUSTRYNAME IS NULL OR e.FIRSTINDUSTRYNAME NOT IN ('银行', '非银金融')
),
amount_rows AS (
    SELECT m.COMPANYCODE, m.ENDDATE, m.NPFROMPARENTCOMPANYOWNERS AS ni_cumulative,
           m.NETOPERATECASHFLOW AS cfo_cumulative, m.MARK AS version_flag,
           m.INFOPUBLDATE, m.JSID, m.ID
    FROM lc_maindatanew m JOIN stock_pool s ON s.COMPANYCODE = m.COMPANYCODE
    WHERE s.LISTEDSECTOR <> 7 AND m.ACCOUNTINGSTANDARDS = 1
      AND m.MARK IN (1, 8, 7, 6, 2) AND m.INFOPUBLDATE < '2025-12-16'
      AND m.ENDDATE BETWEEN '2022-09-30' AND '2025-09-30'
      AND MONTH(m.ENDDATE) IN (3, 6, 9, 12) AND m.ENDDATE = LAST_DAY(m.ENDDATE)
    UNION ALL
    SELECT m.COMPANYCODE, m.ENDDATE, m.NPPARCOMOWNERS AS ni_cumulative,
           m.NETOPERATECASHFLOW AS cfo_cumulative, m.IFADJUSTED AS version_flag,
           m.INFOPUBLDATE, m.JSID, m.ID
    FROM lc_stibmaindata m JOIN stock_pool s ON s.COMPANYCODE = m.COMPANYCODE
    WHERE s.LISTEDSECTOR = 7 AND m.IFMERGED = 1
      AND m.IFADJUSTED IN (1, 8, 7, 6, 2) AND m.INFOPUBLDATE < '2025-12-16'
      AND m.ENDDATE BETWEEN '2022-09-30' AND '2025-09-30'
      AND MONTH(m.ENDDATE) IN (3, 6, 9, 12) AND m.ENDDATE = LAST_DAY(m.ENDDATE)
),
amount_versions AS (
    SELECT m.*, ROW_NUMBER() OVER (
        PARTITION BY COMPANYCODE, ENDDATE
        ORDER BY CASE version_flag WHEN 1 THEN 0 WHEN 8 THEN 1 WHEN 7 THEN 2
                     WHEN 6 THEN 3 WHEN 2 THEN 4 END,
                 INFOPUBLDATE DESC, JSID DESC, ID DESC
    ) AS rn FROM amount_rows m
),
amounts AS (
    SELECT * FROM amount_versions WHERE rn = 1
),
single_quarter AS (
    SELECT m.COMPANYCODE, m.ENDDATE,
           CASE WHEN MONTH(m.ENDDATE) = 3 THEN m.ni_cumulative
                ELSE m.ni_cumulative - b.ni_cumulative END AS ni,
           CASE WHEN MONTH(m.ENDDATE) = 3 THEN m.cfo_cumulative
                ELSE m.cfo_cumulative - b.cfo_cumulative END AS cfo
    FROM amounts m LEFT JOIN amounts b
      ON b.COMPANYCODE = m.COMPANYCODE
     AND b.ENDDATE = LAST_DAY(DATE_SUB(m.ENDDATE, INTERVAL 3 MONTH))
     AND YEAR(b.ENDDATE) = YEAR(m.ENDDATE)
    WHERE m.ENDDATE BETWEEN '2022-12-31' AND '2025-09-30'
),
yoy_versions AS (
    SELECT q.COMPANYCODE, q.ENDDATE, q.OPERATINGREVENUEYOY,
           ROW_NUMBER() OVER (
               PARTITION BY q.COMPANYCODE, q.ENDDATE
               ORDER BY q.INFOPUBLDATE DESC, q.MARK, q.JSID DESC, q.ID DESC
           ) AS rn
    FROM lc_qfinancialindexnew q JOIN stock_pool s ON s.COMPANYCODE = q.COMPANYCODE
    WHERE q.MARK IN (1, 2) AND q.ACCOUNTINGSTANDARDS = 1
      AND q.INFOPUBLDATE < '2025-12-16'
      AND q.ENDDATE BETWEEN '2022-12-31' AND '2025-09-30'
      AND MONTH(q.ENDDATE) IN (3, 6, 9, 12) AND q.ENDDATE = LAST_DAY(q.ENDDATE)
),
series_12q AS (
    SELECT q.*, y.OPERATINGREVENUEYOY
    FROM single_quarter q LEFT JOIN yoy_versions y
      ON y.COMPANYCODE = q.COMPANYCODE AND y.ENDDATE = q.ENDDATE AND y.rn = 1
),
company_stats AS (
    SELECT COMPANYCODE, MAX(OPERATINGREVENUEYOY) - MIN(OPERATINGREVENUEYOY) AS yoy_range,
           SUM(CASE WHEN ni > 0 AND cfo < 0.8 * ni THEN 1 ELSE 0 END) AS low_cash_count,
           COUNT(*) AS quarter_count, COUNT(ni) AS ni_count, COUNT(cfo) AS cfo_count,
           COUNT(OPERATINGREVENUEYOY) AS yoy_count
    FROM series_12q GROUP BY COMPANYCODE
)
-- Final projection: screening and ordering use unrounded values.
SELECT s.SECUCODE AS `股票代码`, s.SECUABBR AS `公司简称`,
       ROUND(c.yoy_range, 4) AS `营收同比极差`, c.low_cash_count AS `低现金回收季度数`
FROM stock_pool s JOIN company_stats c ON c.COMPANYCODE = s.COMPANYCODE
WHERE c.quarter_count = 12 AND c.ni_count = 12 AND c.cfo_count = 12 AND c.yoy_count = 12
  AND c.yoy_range > 50 AND c.low_cash_count >= 8
ORDER BY c.yoy_range DESC, s.SECUCODE;
