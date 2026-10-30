WITH
security_base AS (
    SELECT INNERCODE, COMPANYCODE, SECUCODE, SECUABBR, LISTEDSECTOR
    FROM secumain
    WHERE SECUCATEGORY = 1 AND SECUMARKET IN (83, 90)
      AND LISTEDDATE < '2025-11-28'
),
status_rows AS (
    SELECT l.INNERCODE, l.CHANGEDATE, l.CHANGETYPE, l.INSERTTIME, l.JSID, l.ID
    FROM lc_liststatus l JOIN security_base s ON s.INNERCODE = l.INNERCODE
    WHERE s.LISTEDSECTOR <> 7
    UNION ALL
    SELECT l.INNERCODE, l.CHANGEDATE, l.CHANGETYPE, l.INSERTTIME, l.JSID, l.ID
    FROM lc_stibliststatus l JOIN security_base s ON s.INNERCODE = l.INNERCODE
    WHERE s.LISTEDSECTOR = 7
),
status_versions AS (
    SELECT l.*, ROW_NUMBER() OVER (
        PARTITION BY INNERCODE
        ORDER BY CHANGEDATE DESC, INSERTTIME DESC, JSID DESC, ID DESC
    ) AS rn
    FROM status_rows l
    WHERE CHANGEDATE < '2025-11-28' AND CHANGETYPE IN (1, 2, 3, 4, 5, 6)
      AND COALESCE(INSERTTIME, CHANGEDATE) < '2025-11-28'
),
industry_rows AS (
    SELECT e.COMPANYCODE, e.FIRSTINDUSTRYNAME, e.INFOPUBLDATE, e.JSID, e.ID
    FROM lc_exgindustry e JOIN security_base s ON s.COMPANYCODE = e.COMPANYCODE
    WHERE s.LISTEDSECTOR <> 7 AND e.STANDARD = 38
      AND e.INFOPUBLDATE < '2025-11-28'
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > '2025-11-27')
    UNION ALL
    SELECT e.COMPANYCODE, e.FIRSTINDUSTRYNAME, e.INFOPUBLDATE, e.JSID, e.ID
    FROM lc_stibexgindustry e JOIN security_base s ON s.COMPANYCODE = e.COMPANYCODE
    WHERE s.LISTEDSECTOR = 7 AND e.STANDARD = 38
      AND e.INFOPUBLDATE < '2025-11-28' AND e.EFFECTIVEDATE <= '2025-11-27'
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > '2025-11-27')
),
industry_versions AS (
    SELECT e.*, ROW_NUMBER() OVER (
        PARTITION BY COMPANYCODE ORDER BY INFOPUBLDATE DESC, JSID DESC, ID DESC
    ) AS rn FROM industry_rows e
),
stock_pool AS (
    SELECT s.* FROM security_base s
    JOIN status_versions l ON l.INNERCODE = s.INNERCODE AND l.rn = 1
    JOIN industry_versions e ON e.COMPANYCODE = s.COMPANYCODE AND e.rn = 1
    WHERE l.CHANGETYPE IN (1, 2, 3, 6) AND e.FIRSTINDUSTRYNAME = '机械设备'
),
amount_rows AS (
    SELECT m.COMPANYCODE, m.TOTALASSETS, m.MARK AS version_flag,
           m.INFOPUBLDATE, m.JSID, m.ID
    FROM lc_maindatanew m JOIN stock_pool s ON s.COMPANYCODE = m.COMPANYCODE
    WHERE s.LISTEDSECTOR <> 7 AND m.ACCOUNTINGSTANDARDS = 1
      AND m.MARK IN (1, 8, 7, 6, 2)
      AND m.ENDDATE = '2025-09-30' AND m.INFOPUBLDATE < '2025-11-28'
    UNION ALL
    SELECT m.COMPANYCODE, m.TOTALASSETS, m.IFADJUSTED AS version_flag,
           m.INFOPUBLDATE, m.JSID, m.ID
    FROM lc_stibmaindata m JOIN stock_pool s ON s.COMPANYCODE = m.COMPANYCODE
    WHERE s.LISTEDSECTOR = 7 AND m.IFMERGED = 1
      AND m.IFADJUSTED IN (1, 8, 7, 6, 2)
      AND m.ENDDATE = '2025-09-30' AND m.INFOPUBLDATE < '2025-11-28'
),
amount_versions AS (
    SELECT m.*, ROW_NUMBER() OVER (
        PARTITION BY COMPANYCODE
        ORDER BY CASE version_flag WHEN 1 THEN 0 WHEN 8 THEN 1 WHEN 7 THEN 2
                     WHEN 6 THEN 3 WHEN 2 THEN 4 END,
                 INFOPUBLDATE DESC, JSID DESC, ID DESC
    ) AS rn FROM amount_rows m
),
fin_versions AS (
    SELECT f.*, ROW_NUMBER() OVER (
        PARTITION BY f.COMPANYCODE
        ORDER BY CASE f.MARK WHEN 1 THEN 0 WHEN 8 THEN 1 WHEN 7 THEN 2
                     WHEN 6 THEN 3 WHEN 2 THEN 4 END,
                 f.INFOPUBLDATE DESC, f.JSID DESC, f.ID DESC
    ) AS rn
    FROM fin_derivative f JOIN stock_pool s ON s.COMPANYCODE = f.COMPANYCODE
    WHERE f.ENDDATE = '2025-09-30' AND f.INFOPUBLDATE < '2025-11-28'
      AND f.MARK IN (1, 8, 7, 6, 2)
),
valuation_rows AS (
    SELECT v.INNERCODE, v.TOTALMV, v.JSID, v.ID
    FROM lc_dindicesforvaluation v JOIN stock_pool s ON s.INNERCODE = v.INNERCODE
    WHERE s.LISTEDSECTOR <> 7 AND v.TRADINGDAY = '2025-11-27'
    UNION ALL
    SELECT v.INNERCODE, v.TOTALMV, v.JSID, v.ID
    FROM lc_stibdindiforvalue v JOIN stock_pool s ON s.INNERCODE = v.INNERCODE
    WHERE s.LISTEDSECTOR = 7 AND v.TRADINGDAY = '2025-11-27'
),
valuation_versions AS (
    SELECT v.*, ROW_NUMBER() OVER (
        PARTITION BY INNERCODE ORDER BY JSID DESC, ID DESC
    ) AS rn FROM valuation_rows v
),
metrics AS (
    SELECT s.*, m.TOTALASSETS, f.WROE, f.FIXEDASSETTRATE, v.TOTALMV,
           v.TOTALMV / NULLIF(m.TOTALASSETS, 0) AS simplified_q
    FROM stock_pool s
    LEFT JOIN amount_versions m ON m.COMPANYCODE = s.COMPANYCODE AND m.rn = 1
    LEFT JOIN fin_versions f ON f.COMPANYCODE = s.COMPANYCODE AND f.rn = 1
    LEFT JOIN valuation_versions v ON v.INNERCODE = s.INNERCODE AND v.rn = 1
)
-- Final projection: screening and ordering use unrounded values.
SELECT SECUCODE AS `股票代码`, SECUABBR AS `证券简称`,
       ROUND(TOTALMV, 2) AS `总市值`, ROUND(TOTALASSETS, 2) AS `总资产`,
       ROUND(simplified_q, 4) AS `简化Q`, ROUND(WROE, 2) AS `加权ROE`,
       ROUND(FIXEDASSETTRATE, 4) AS `固定资产周转率`
FROM metrics
WHERE TOTALASSETS > 0 AND TOTALMV > 0 AND TOTALMV < TOTALASSETS
  AND WROE > 12 AND FIXEDASSETTRATE > 0.9
ORDER BY simplified_q, SECUCODE;
