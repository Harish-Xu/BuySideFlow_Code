WITH params AS (
    SELECT DATE('2025-07-15') AS as_of_date,
           TIMESTAMP('2025-07-16 00:00:00') AS next_day
),
stock_pool AS (
    SELECT DISTINCT s.COMPANYCODE, s.SECUCODE, s.SECUABBR
    FROM secumain idx
    JOIN lc_indexcomponent ic ON ic.INDEXINNERCODE = idx.INNERCODE
    JOIN secumain s ON s.INNERCODE = ic.SECUINNERCODE
    JOIN params p ON 1 = 1
    WHERE idx.SECUCODE = '000852'
      AND idx.SECUMARKET = 83
      AND s.SECUCATEGORY = 1
      AND s.SECUMARKET = 83
      AND s.LISTEDSECTOR = 1
      AND ic.INDATE <= p.as_of_date
      AND (ic.OUTDATE IS NULL OR ic.OUTDATE > p.as_of_date)
),
industry AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME
    FROM (
        SELECT i.COMPANYCODE, i.FIRSTINDUSTRYNAME,
               ROW_NUMBER() OVER (
                   PARTITION BY i.COMPANYCODE
                   ORDER BY i.INFOPUBLDATE DESC, i.INSERTTIME DESC, i.ID DESC
               ) AS rn
        FROM lc_exgindustry i
        JOIN stock_pool s ON s.COMPANYCODE = i.COMPANYCODE
        JOIN params p ON 1 = 1
        WHERE i.STANDARD = 38
          AND i.INFOPUBLDATE < p.next_day
          AND i.INSERTTIME < p.next_day
          AND (i.CANCELDATE IS NULL OR i.CANCELDATE > p.as_of_date)
    ) v
    WHERE rn = 1
),
annual_report AS (
    SELECT COMPANYCODE, NPFROMPARENTCOMPANYOWNERS,
           NETOPERATECASHFLOW, NETINVESTCASHFLOW, NETFINANCECASHFLOW
    FROM (
        SELECT m.COMPANYCODE, m.NPFROMPARENTCOMPANYOWNERS,
               m.NETOPERATECASHFLOW, m.NETINVESTCASHFLOW,
               m.NETFINANCECASHFLOW,
               ROW_NUMBER() OVER (
                   PARTITION BY m.COMPANYCODE
                   ORDER BY m.INFOPUBLDATE DESC, m.INSERTTIME DESC,
                            m.JSID DESC, m.ID DESC
               ) AS rn
        FROM lc_maindatanew m
        JOIN stock_pool s ON s.COMPANYCODE = m.COMPANYCODE
        JOIN params p ON 1 = 1
        WHERE m.ENDDATE = '2024-12-31'
          AND m.ACCOUNTINGSTANDARDS = 1
          AND m.INFOSOURCECODE IN (110101, 120102)
          AND m.INFOPUBLDATE < p.next_day
          AND m.INSERTTIME < p.next_day
    ) v
    WHERE rn = 1
)
SELECT s.SECUCODE AS `股票代码`,
       s.SECUABBR AS `股票简称`,
       ROUND(f.NPFROMPARENTCOMPANYOWNERS / 100000000, 2) AS `归母净利润(亿元)`,
       ROUND(f.NETOPERATECASHFLOW / 100000000, 2) AS `经营现金流净额(亿元)`,
       ROUND(f.NETINVESTCASHFLOW / 100000000, 2) AS `投资现金流净额(亿元)`,
       ROUND(f.NETFINANCECASHFLOW / 100000000, 2) AS `筹资现金流净额(亿元)`
FROM stock_pool s
JOIN industry i ON i.COMPANYCODE = s.COMPANYCODE
JOIN annual_report f ON f.COMPANYCODE = s.COMPANYCODE
WHERE i.FIRSTINDUSTRYNAME = '机械设备'
  AND f.NPFROMPARENTCOMPANYOWNERS > 0
  AND f.NETOPERATECASHFLOW > 0
  AND f.NETINVESTCASHFLOW < 0
  AND f.NETFINANCECASHFLOW IS NOT NULL
ORDER BY s.SECUCODE;
