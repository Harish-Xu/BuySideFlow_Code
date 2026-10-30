WITH
security_base AS (
    SELECT INNERCODE, COMPANYCODE, SECUCODE, SECUABBR, LISTEDSECTOR
    FROM secumain
    WHERE SECUCATEGORY = 1 AND SECUMARKET IN (83, 90)
      AND LISTEDSECTOR IN (1, 2) AND LISTEDDATE < '2025-09-02'
),
status_versions AS (
    SELECT l.*, ROW_NUMBER() OVER (
        PARTITION BY l.INNERCODE
        ORDER BY l.CHANGEDATE DESC, l.INSERTTIME DESC, l.JSID DESC, l.ID DESC
    ) AS rn
    FROM lc_liststatus l JOIN security_base s ON s.INNERCODE = l.INNERCODE
    WHERE l.CHANGEDATE < '2025-09-02' AND l.CHANGETYPE IN (1, 2, 3, 4, 5, 6)
      AND COALESCE(l.INSERTTIME, l.CHANGEDATE) < '2025-09-02'
),
stock_pool AS (
    SELECT s.* FROM security_base s
    JOIN status_versions l ON l.INNERCODE = s.INNERCODE AND l.rn = 1
    WHERE l.CHANGETYPE IN (1, 2, 3, 6)
),
latest_period AS (
    SELECT m.COMPANYCODE, MAX(m.ENDDATE) AS ENDDATE
    FROM lc_maindatanew m JOIN stock_pool s ON s.COMPANYCODE = m.COMPANYCODE
    WHERE m.MARK IN (1, 8, 7, 6, 2) AND m.ACCOUNTINGSTANDARDS = 1
      AND m.INFOPUBLDATE < '2025-09-02'
      AND m.ENDDATE BETWEEN '1990-01-01' AND '2025-06-30'
      AND MONTH(m.ENDDATE) IN (3, 6, 9, 12) AND m.ENDDATE = LAST_DAY(m.ENDDATE)
    GROUP BY m.COMPANYCODE
),
amount_versions AS (
    SELECT m.COMPANYCODE, m.ENDDATE, m.NPFROMPARENTCOMPANYOWNERS,
           m.SEWITHOUTMI,
           ROW_NUMBER() OVER (
               PARTITION BY m.COMPANYCODE, m.ENDDATE
               ORDER BY CASE m.MARK WHEN 1 THEN 0 WHEN 8 THEN 1 WHEN 7 THEN 2
                            WHEN 6 THEN 3 WHEN 2 THEN 4 END,
                        m.INFOPUBLDATE DESC, m.JSID DESC, m.ID DESC
           ) AS rn
    FROM lc_maindatanew m JOIN latest_period l ON l.COMPANYCODE = m.COMPANYCODE
    WHERE m.MARK IN (1, 8, 7, 6, 2) AND m.ACCOUNTINGSTANDARDS = 1
      AND m.INFOPUBLDATE < '2025-09-02'
      AND m.ENDDATE BETWEEN DATE_SUB(l.ENDDATE, INTERVAL 9 MONTH) AND l.ENDDATE
      AND MONTH(m.ENDDATE) IN (3, 6, 9, 12) AND m.ENDDATE = LAST_DAY(m.ENDDATE)
),
amounts AS (
    SELECT * FROM amount_versions WHERE rn = 1
),
single_quarter AS (
    SELECT m.COMPANYCODE, m.ENDDATE, m.SEWITHOUTMI,
           CASE WHEN MONTH(m.ENDDATE) = 3 THEN m.NPFROMPARENTCOMPANYOWNERS
                ELSE m.NPFROMPARENTCOMPANYOWNERS - b.NPFROMPARENTCOMPANYOWNERS END AS ni
    FROM amounts m LEFT JOIN amounts b
      ON b.COMPANYCODE = m.COMPANYCODE
     AND b.ENDDATE = LAST_DAY(DATE_SUB(m.ENDDATE, INTERVAL 3 MONTH))
     AND YEAR(b.ENDDATE) = YEAR(m.ENDDATE)
),
yoy_versions AS (
    SELECT q.COMPANYCODE, q.ENDDATE, q.OPERATINGREVENUEYOY,
           ROW_NUMBER() OVER (
               PARTITION BY q.COMPANYCODE, q.ENDDATE
               ORDER BY q.INFOPUBLDATE DESC, q.MARK, q.JSID DESC, q.ID DESC
           ) AS rn
    FROM lc_qfinancialindexnew q JOIN latest_period l
      ON l.COMPANYCODE = q.COMPANYCODE AND l.ENDDATE = q.ENDDATE
    WHERE q.MARK IN (1, 2) AND q.ACCOUNTINGSTANDARDS = 1
      AND q.INFOPUBLDATE < '2025-09-02'
      AND q.ENDDATE BETWEEN '1990-01-01' AND '2025-06-30'
),
metrics AS (
    SELECT s.*, l.ENDDATE, c.ni AS current_ni, b.ni AS previous_ni,
           c.SEWITHOUTMI AS current_equity, b.SEWITHOUTMI AS previous_equity,
           y.OPERATINGREVENUEYOY,
           400.0 * c.ni / NULLIF(c.SEWITHOUTMI, 0) AS current_roe,
           400.0 * b.ni / NULLIF(b.SEWITHOUTMI, 0) AS previous_roe
    FROM stock_pool s LEFT JOIN latest_period l ON l.COMPANYCODE = s.COMPANYCODE
    LEFT JOIN single_quarter c ON c.COMPANYCODE = l.COMPANYCODE AND c.ENDDATE = l.ENDDATE
    LEFT JOIN single_quarter b ON b.COMPANYCODE = l.COMPANYCODE
      AND b.ENDDATE = LAST_DAY(DATE_SUB(l.ENDDATE, INTERVAL 3 MONTH))
    LEFT JOIN yoy_versions y ON y.COMPANYCODE = l.COMPANYCODE AND y.ENDDATE = l.ENDDATE AND y.rn = 1
)
-- Final projection: screening and ordering use unrounded values.
SELECT SECUCODE AS `股票代码`, SECUABBR AS `公司简称`,
       DATE_FORMAT(ENDDATE, '%Y-%m-%d') AS `最新报告期`,
       ROUND(current_roe, 4) AS `最新单季年化摊薄ROE`,
       ROUND(current_roe - previous_roe, 4) AS `年化摊薄ROE环比变化`
FROM metrics
WHERE current_ni > 0 AND previous_ni > 0
  AND current_equity > 0 AND previous_equity > 0 AND OPERATINGREVENUEYOY > 10
  AND current_ni * previous_equity > previous_ni * current_equity
ORDER BY current_roe DESC, SECUCODE
LIMIT 10;
