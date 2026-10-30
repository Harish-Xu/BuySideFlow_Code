-- name: stock_pool
WITH stock_base AS (
    SELECT s.COMPANYCODE, s.INNERCODE, s.SECUCODE,
           COALESCE(NULLIF(s.SECUABBR, ''), s.CHINAMEABBR, s.CHINAME) AS stock_name
    FROM secumain s
    WHERE s.SECUCATEGORY = 1 AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR IN (1, 2, 6)
      AND s.LISTEDDATE < '${next_date}'
      AND s.SECUCODE NOT LIKE '200%' AND s.SECUCODE NOT LIKE '900%'
      AND s.COMPANYCODE IS NOT NULL
), industries AS (
    SELECT e.COMPANYCODE, e.FIRSTINDUSTRYNAME,
           ROW_NUMBER() OVER (
               PARTITION BY e.COMPANYCODE
               ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC, e.ID DESC
           ) AS rn
    FROM lc_exgindustry e
    JOIN (SELECT DISTINCT COMPANYCODE FROM stock_base) s ON s.COMPANYCODE=e.COMPANYCODE
    WHERE e.STANDARD=38 AND e.INFOPUBLDATE < '${next_date}'
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > '${obs_date}')
), listed AS (
    SELECT ls.INNERCODE, ls.CHANGETYPE,
           ROW_NUMBER() OVER (
               PARTITION BY ls.INNERCODE
               ORDER BY ls.CHANGEDATE DESC, ls.INSERTTIME DESC, ls.JSID DESC, ls.ID DESC
           ) AS rn
    FROM lc_liststatus ls JOIN stock_base s ON s.INNERCODE=ls.INNERCODE
    WHERE ls.SECUMARKET IN (83, 90) AND ls.CHANGETYPE BETWEEN 1 AND 9
      AND ls.CHANGEDATE < '${next_date}' AND ls.INSERTTIME < '${next_date}'
)
SELECT s.COMPANYCODE, s.INNERCODE, LPAD(s.SECUCODE, 6, '0') AS stock_code,
       TRIM(s.stock_name) AS stock_name
FROM stock_base s
JOIN industries i ON i.COMPANYCODE=s.COMPANYCODE AND i.rn=1
JOIN listed l ON l.INNERCODE=s.INNERCODE AND l.rn=1
WHERE l.CHANGETYPE IN (1, 3, 6)
  AND i.FIRSTINDUSTRYNAME NOT IN ('银行', '非银金融', '非银行金融')
  AND NOT EXISTS (
      SELECT 1 FROM cs_riskalert ra
      WHERE ra.INNERCODE=s.INNERCODE AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
        AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME) < '${next_date}'
        AND ra.IMPLEMENTDATE < '${next_date}'
        AND (ra.REMOVEDATE IS NULL OR ra.REMOVEDATE >= '${next_date}'
             OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.REMOVEDATE) >= '${next_date}')
  )
ORDER BY s.SECUCODE, s.INNERCODE

-- name: financial_records
SELECT m.COMPANYCODE, YEAR(m.ENDDATE) AS fiscal_year, m.MARK,
       m.NPFROMPARENTCOMPANYOWNERS AS parent_net_profit,
       m.NETOPERATECASHFLOW AS operating_cashflow,
       m.INFOPUBLDATE AS pub_date, m.JSID, m.ID
FROM lc_maindatanew m
WHERE m.COMPANYCODE IN (${company_values})
  AND m.MARK IN (1, 8, 7, 6, 2) AND m.ACCOUNTINGSTANDARDS=1
  AND m.ENDDATE IN (${annual_enddate_list_sql})
  AND m.INFOPUBLDATE < '${next_date}'
