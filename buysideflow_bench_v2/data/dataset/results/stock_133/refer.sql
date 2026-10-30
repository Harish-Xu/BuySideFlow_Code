-- 代码、简称采用现行主表标识，仅作展示；上市资格与审计意见按观察日回溯。
-- 上市历史的 INSERTTIME 存在事后补录，不作为交易所上市事件的公开日期。
WITH params AS (
    SELECT DATE('2025-04-29') AS as_of_date
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
        FROM (
            SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID, SECUMARKET
            FROM lc_liststatus
            UNION ALL
            SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID, 83 AS SECUMARKET
            FROM lc_stibliststatus
        ) ls
        JOIN params p
          ON ls.CHANGEDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE ls.SECUMARKET IN (83, 90, 18)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
stock_seed AS (
    SELECT
        s.COMPANYCODE,
        MIN(s.INNERCODE) AS INNERCODE
    FROM secumain s
    JOIN params p
      ON s.LISTEDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    LEFT JOIN listed_asof la
      ON la.INNERCODE = s.INNERCODE
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90, 18)
      AND s.LISTEDSECTOR IN (1, 2, 6, 7, 8)
      AND la.CHANGETYPE IN (1, 2, 3, 6)
    GROUP BY s.COMPANYCODE
),
stock_base AS (
    SELECT
        s.COMPANYCODE,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name,
        s.LISTEDSECTOR
    FROM secumain s
    JOIN stock_seed ss
      ON ss.INNERCODE = s.INNERCODE
),
audit_dedup AS (
    SELECT
        a.COMPANYCODE,
        a.INFOPUBLDATE,
        a.OPINIONTYPE,
        ROW_NUMBER() OVER (
            PARTITION BY a.COMPANYCODE, a.ENDDATE, a.AUDITREPORTSTYPE
            ORDER BY a.INFOPUBLDATE DESC, a.INSERTTIME DESC, a.JSID DESC
        ) AS rn
    FROM lc_auditopinion a
    JOIN params p
      ON a.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE a.AUDITREPORTSTYPE = 1
      AND a.ENDDATE = DATE('2024-12-31')
)
SELECT
    sb.SECUCODE AS `股票代码`,
    sb.stock_name AS `股票简称`,
    CASE ad.OPINIONTYPE
        WHEN 4 THEN '无法表示意见'
        WHEN 5 THEN '否定意见'
    END AS `审计意见`,
    DATE_FORMAT(ad.INFOPUBLDATE, '%Y-%m-%d') AS `公告日期`
FROM audit_dedup ad
JOIN stock_base sb
  ON sb.COMPANYCODE = ad.COMPANYCODE
WHERE ad.rn = 1
  AND ad.OPINIONTYPE IN (4, 5)
ORDER BY sb.SECUCODE;
