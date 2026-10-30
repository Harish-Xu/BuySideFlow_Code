WITH params AS (
    SELECT DATE('2023-11-01') AS cutoff
),
stock_base AS (
    SELECT s.INNERCODE, s.COMPANYCODE, s.SECUCODE, s.SECUABBR
    FROM secumain s
    CROSS JOIN params p
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR IN (1, 2)
      AND s.LISTEDDATE < p.cutoff
      AND s.COMPANYCODE IS NOT NULL
      AND s.SECUCODE NOT LIKE 'X%'
),
listed_ranked AS (
    SELECT
        ls.INNERCODE,
        ls.CHANGETYPE,
        ROW_NUMBER() OVER (
            PARTITION BY ls.INNERCODE
            ORDER BY ls.CHANGEDATE DESC, ls.INSERTTIME DESC, ls.JSID DESC, ls.ID DESC
        ) AS rn
    FROM lc_liststatus ls
    JOIN stock_base s ON s.INNERCODE = ls.INNERCODE
    CROSS JOIN params p
    WHERE ls.SECUMARKET IN (83, 90)
      AND ls.CHANGETYPE BETWEEN 1 AND 9
      AND ls.CHANGEDATE < p.cutoff
      AND ls.INSERTTIME < p.cutoff
),
industry_ranked AS (
    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYNAME,
        ROW_NUMBER() OVER (
            PARTITION BY e.COMPANYCODE
            ORDER BY e.INFOPUBLDATE DESC, e.INSERTTIME DESC, e.JSID DESC, e.ID DESC
        ) AS rn
    FROM lc_exgindustry e
    JOIN (SELECT DISTINCT COMPANYCODE FROM stock_base) s ON s.COMPANYCODE = e.COMPANYCODE
    CROSS JOIN params p
    WHERE e.STANDARD = 38
      AND e.INFOPUBLDATE < p.cutoff
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE >= p.cutoff)
),
stock_pool AS (
    SELECT COMPANYCODE, SECUCODE, SECUABBR, FIRSTINDUSTRYNAME
    FROM (
        SELECT
            s.COMPANYCODE, s.SECUCODE, s.SECUABBR, i.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE ORDER BY s.SECUCODE, s.INNERCODE
            ) AS rn
        FROM stock_base s
        JOIN listed_ranked l ON l.INNERCODE = s.INNERCODE AND l.rn = 1
        JOIN industry_ranked i ON i.COMPANYCODE = s.COMPANYCODE AND i.rn = 1
        WHERE l.CHANGETYPE IN (1, 3, 6)
          AND i.FIRSTINDUSTRYNAME = '机械设备'
    ) ranked
    WHERE rn = 1
),
quarter_yoy_ranked AS (
 SELECT q.COMPANYCODE,q.ENDDATE,q.OPERATINGREVENUEYOY,
 ROW_NUMBER() OVER(PARTITION BY q.COMPANYCODE,q.ENDDATE ORDER BY q.INFOPUBLDATE DESC,q.MARK,q.JSID DESC,q.ID DESC) AS rn
 FROM lc_qfinancialindexnew q JOIN stock_pool s ON s.COMPANYCODE=q.COMPANYCODE
 WHERE q.ENDDATE IN ('2023-06-30','2023-09-30') AND q.INFOPUBLDATE<'2023-11-01'
 AND q.MARK IN (1,2) AND q.ACCOUNTINGSTANDARDS=1
),quarter_panel AS (
 SELECT COMPANYCODE,
 MAX(CASE WHEN ENDDATE='2023-06-30' THEN OPERATINGREVENUEYOY END) AS q2_growth,
 MAX(CASE WHEN ENDDATE='2023-09-30' THEN OPERATINGREVENUEYOY END) AS q3_growth
 FROM quarter_yoy_ranked WHERE rn=1 GROUP BY COMPANYCODE
)
SELECT s.SECUCODE AS `证券代码`,s.SECUABBR AS `证券简称`,ROUND(q.q2_growth,2) AS `第二季度营业收入同比增速`,
ROUND(q.q3_growth,2) AS `第三季度营业收入同比增速`,ROUND(q.q3_growth-q.q2_growth,2) AS `增速提高幅度`
FROM stock_pool s JOIN quarter_panel q ON q.COMPANYCODE=s.COMPANYCODE
WHERE q.q2_growth IS NOT NULL AND q.q3_growth>20 AND q.q3_growth-q.q2_growth>10
ORDER BY q.q3_growth-q.q2_growth DESC,s.SECUCODE;
