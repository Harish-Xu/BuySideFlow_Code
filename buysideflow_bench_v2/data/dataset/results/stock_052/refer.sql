WITH
target_index AS (
    SELECT INNERCODE FROM secumain
    WHERE SECUCATEGORY = 4 AND SECUCODE = '399006'
    ORDER BY INNERCODE LIMIT 1
),
stock_pool AS (
    SELECT DISTINCT s.INNERCODE, s.COMPANYCODE, s.SECUCODE, s.SECUABBR, s.LISTEDSECTOR
    FROM lc_indexcomponent i
    JOIN target_index t ON t.INNERCODE = i.INDEXINNERCODE
    JOIN secumain s ON s.INNERCODE = i.SECUINNERCODE
    WHERE i.INDATE <= '2025-10-29' AND (i.OUTDATE IS NULL OR i.OUTDATE > '2025-10-29')
),
quarter_versions AS (
    SELECT q.COMPANYCODE, q.ENDDATE, q.ROE, q.NETPROFITRATIO, q.TOTALASSETTRATE,
           ROW_NUMBER() OVER (
               PARTITION BY q.COMPANYCODE, q.ENDDATE
               ORDER BY q.INFOPUBLDATE DESC, q.MARK, q.JSID DESC, q.ID DESC
           ) AS rn
    FROM lc_qfinancialindexnew q JOIN stock_pool s ON s.COMPANYCODE = q.COMPANYCODE
    WHERE q.MARK IN (1, 2) AND q.ACCOUNTINGSTANDARDS = 1
      AND q.INFOPUBLDATE < '2025-10-29'
      AND q.ENDDATE IN ('2024-09-30', '2025-09-30')
),
amount_versions AS (
    SELECT m.COMPANYCODE, m.ENDDATE, m.NPFROMPARENTCOMPANYOWNERS,
           ROW_NUMBER() OVER (
               PARTITION BY m.COMPANYCODE, m.ENDDATE
               ORDER BY CASE m.MARK WHEN 1 THEN 0 WHEN 8 THEN 1 WHEN 7 THEN 2
                            WHEN 6 THEN 3 WHEN 2 THEN 4 END,
                        m.INFOPUBLDATE DESC, m.JSID DESC, m.ID DESC
           ) AS rn
    FROM lc_maindatanew m JOIN stock_pool s ON s.COMPANYCODE = m.COMPANYCODE
    WHERE m.MARK IN (1, 8, 7, 6, 2) AND m.ACCOUNTINGSTANDARDS = 1
      AND m.INFOPUBLDATE < '2025-10-29'
      AND m.ENDDATE IN ('2025-06-30', '2025-09-30')
),
metrics AS (
    SELECT s.*, c.ROE AS current_roe, b.ROE AS previous_roe,
           c.NETPROFITRATIO AS current_margin, b.NETPROFITRATIO AS previous_margin,
           c.TOTALASSETTRATE AS current_turnover, b.TOTALASSETTRATE AS previous_turnover,
           m.NPFROMPARENTCOMPANYOWNERS - h.NPFROMPARENTCOMPANYOWNERS AS current_ni
    FROM stock_pool s
    LEFT JOIN quarter_versions c ON c.COMPANYCODE = s.COMPANYCODE AND c.rn = 1 AND c.ENDDATE = '2025-09-30'
    LEFT JOIN quarter_versions b ON b.COMPANYCODE = s.COMPANYCODE AND b.rn = 1 AND b.ENDDATE = '2024-09-30'
    LEFT JOIN amount_versions m ON m.COMPANYCODE = s.COMPANYCODE AND m.rn = 1 AND m.ENDDATE = '2025-09-30'
    LEFT JOIN amount_versions h ON h.COMPANYCODE = s.COMPANYCODE AND h.rn = 1 AND h.ENDDATE = '2025-06-30'
)
-- Final projection: screening and ordering use unrounded values.
SELECT SECUCODE AS `股票代码`, SECUABBR AS `公司简称`,
       ROUND(current_roe - previous_roe, 4) AS `ROE提升`,
       ROUND(current_margin - previous_margin, 4) AS `净利率提升`
FROM metrics
WHERE current_ni > 0 AND current_roe > 0 AND current_margin > 0
  AND current_roe > previous_roe AND current_margin > previous_margin
  AND current_turnover >= previous_turnover
ORDER BY current_roe - previous_roe DESC, SECUCODE;
