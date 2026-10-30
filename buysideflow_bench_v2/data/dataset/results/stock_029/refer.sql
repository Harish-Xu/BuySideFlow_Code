WITH
params AS (
    SELECT TIMESTAMP('2025-09-02 00:00:00') AS next_day_ts,
           DATE('2025-09-01') AS as_of_date,
           DATE('2025-06-30') AS report_date
),
listed_asof AS (
    SELECT INNERCODE, CHANGETYPE
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY ls.CHANGEDATE DESC,
                         COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
                         ls.JSID DESC,
                         ls.ID DESC
            ) AS rn
        FROM lc_liststatus ls
        JOIN params p
          ON ls.CHANGEDATE < p.next_day_ts
         AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) < p.next_day_ts
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
stock_base AS (
    SELECT
        INNERCODE,
        COMPANYCODE,
        SECUCODE,
        stock_name
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUCODE,
            COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.INNERCODE
            ) AS rn
        FROM secumain s
        JOIN params p
          ON s.LISTEDDATE < p.next_day_ts
        JOIN listed_asof la
          ON la.INNERCODE = s.INNERCODE
         AND la.CHANGETYPE IN (1, 2, 3, 6)
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
    ) t
    WHERE rn = 1
),
industry_asof AS (
    SELECT COMPANYCODE, SECONDINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.SECONDINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC,
                         e.JSID DESC,
                         e.ID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN params p
          ON e.INFOPUBLDATE < p.next_day_ts
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    ) t
    WHERE rn = 1
),
mainindex_ver AS (
    SELECT
        m.COMPANYCODE,
        m.ROETTM,
        m.OPERATINGREVENUEGROWRATE,
        m.EQUITYMULTIPLER,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC, m.ID DESC
        ) AS rn_ver
    FROM lc_mainindexnew m
    JOIN params p
      ON m.ENDDATE = p.report_date
     AND m.INFOPUBLDATE < p.next_day_ts
)
SELECT
    sb.SECUCODE AS `代码`,
    sb.stock_name AS `名称`,
    ROUND(m.ROETTM, 2) AS `TTM ROE`,
    ROUND(m.EQUITYMULTIPLER, 2) AS `权益乘数`,
    ROUND(m.OPERATINGREVENUEGROWRATE, 2) AS `营业收入同比增速`
FROM stock_base sb
JOIN industry_asof i
  ON i.COMPANYCODE = sb.COMPANYCODE
JOIN mainindex_ver m
  ON m.COMPANYCODE = sb.COMPANYCODE
 AND m.rn_ver = 1
WHERE i.SECONDINDUSTRYNAME = '证券Ⅱ'
  AND m.ROETTM > 8
  AND m.EQUITYMULTIPLER < 7
  AND m.OPERATINGREVENUEGROWRATE > 5
ORDER BY m.ROETTM DESC,
         sb.SECUCODE ASC;
