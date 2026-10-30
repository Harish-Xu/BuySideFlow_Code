WITH
params AS (
    SELECT
        DATE('2025-11-03') AS as_of_date,
        TIMESTAMP('2025-11-03 23:59:59') AS as_of_dt
),
target_dates AS (
    SELECT DATE('2024-09-30') AS end_date UNION ALL
    SELECT DATE('2025-09-30')
),
list_status_asof AS (
    SELECT
        INNERCODE,
        change_type
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE AS change_type,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY ls.CHANGEDATE DESC,
                         COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
                         ls.JSID DESC
            ) AS rn
        FROM (
            SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID, SECUMARKET
            FROM lc_liststatus
            UNION ALL
            SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID, 83 AS SECUMARKET
            FROM lc_stibliststatus
        ) ls
        JOIN params p
          ON ls.CHANGEDATE <= p.as_of_dt
         AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) <= p.as_of_dt
        WHERE ls.SECUMARKET IN (18, 83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
tradable_asof AS (
    SELECT q.INNERCODE
    FROM qt_dailyquote q
    JOIN params p
      ON q.TRADINGDAY >= p.as_of_date
     AND q.TRADINGDAY < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE q.CLOSEPRICE > 0
    GROUP BY q.INNERCODE
    UNION
    SELECT q.INNERCODE
    FROM lc_stibdailyquote q
    JOIN params p
      ON q.TRADINGDAY >= p.as_of_date
     AND q.TRADINGDAY < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE q.CLOSEPRICE > 0
    GROUP BY q.INNERCODE
),
stock_base AS (
    SELECT
        COMPANYCODE,
        INNERCODE,
        SECUCODE,
        LISTEDSECTOR,
        company
    FROM (
        SELECT
            s.COMPANYCODE,
            s.INNERCODE,
            s.SECUCODE,
            s.LISTEDSECTOR,
            COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS company,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY CASE
                             WHEN s.SECUMARKET IN (83, 90) AND s.LISTEDSECTOR IN (1, 2, 6, 7) THEN 1
                             WHEN s.SECUMARKET = 18 AND s.LISTEDSECTOR = 8 THEN 2
                             ELSE 9
                         END,
                         s.SECUCODE,
                         s.INNERCODE
            ) AS rn
        FROM secumain s
        JOIN params p
          ON s.LISTEDDATE <= p.as_of_date
        LEFT JOIN list_status_asof lsa
          ON lsa.INNERCODE = s.INNERCODE
        JOIN tradable_asof ta
          ON ta.INNERCODE = s.INNERCODE
        WHERE s.SECUCATEGORY = 1
          AND (
              (s.SECUMARKET IN (83, 90) AND s.LISTEDSECTOR IN (1, 2, 6, 7))
              OR (s.SECUMARKET = 18 AND s.LISTEDSECTOR = 8)
          )
          AND lsa.change_type IN (1, 3)
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              WHERE ra.INNERCODE = s.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 9, 12, 14)
                AND DATE(ra.IMPLEMENTDATE) <= p.as_of_date
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= p.as_of_dt
                AND (
                    ra.REMOVEDATE IS NULL
                    OR DATE(ra.REMOVEDATE) > p.as_of_date
                    OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) > p.as_of_dt
                )
          )
    ) s
    WHERE rn = 1
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
                         e.CANCELDATE DESC,
                         e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR <> 7
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_dt
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_dt)
          AND e.FIRSTINDUSTRYNAME = '有色金属'
        UNION ALL
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.EFFECTIVEDATE DESC,
                         e.INFOPUBLDATE DESC,
                         e.CANCELDATE DESC,
                         e.JSID DESC
            ) AS rn
        FROM lc_stibexgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR = 7
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_dt
        WHERE e.STANDARD = 38
          AND e.EFFECTIVEDATE <= p.as_of_dt
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_dt)
          AND e.FIRSTINDUSTRYNAME = '有色金属'
    ) t
    WHERE rn = 1
),
candidate AS (
    SELECT
        sb.COMPANYCODE,
        sb.SECUCODE,
        sb.company,
        ia.FIRSTINDUSTRYNAME AS industry
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
),
financial_ranked AS (
    SELECT
        q.COMPANYCODE,
        DATE(q.ENDDATE) AS end_date,
        q.TOTALASSETTRATE,
        q.ROA,
        ROW_NUMBER() OVER (
            PARTITION BY q.COMPANYCODE, DATE(q.ENDDATE)
            ORDER BY CASE q.MARK
                         WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                         WHEN 6 THEN 4 WHEN 2 THEN 5 ELSE 9
                     END,
                     q.INFOPUBLDATE DESC,
                     q.INSERTTIME DESC,
                     q.JSID DESC,
                     q.ID DESC
        ) AS rn
    FROM lc_qfinancialindexnew q
    JOIN candidate c
      ON c.COMPANYCODE = q.COMPANYCODE
    JOIN params p
      ON q.INFOPUBLDATE <= p.as_of_dt
    JOIN target_dates td
      ON td.end_date = DATE(q.ENDDATE)
    WHERE COALESCE(q.ACCOUNTINGSTANDARDS, 1) = 1
      AND q.MARK IN (1, 8, 7, 6, 2)
      AND q.TOTALASSETTRATE IS NOT NULL
      AND q.ROA IS NOT NULL
),
financial_points AS (
    SELECT
        COMPANYCODE,
        end_date,
        TOTALASSETTRATE,
        ROA
    FROM financial_ranked
    WHERE rn = 1
),
quarter_panel AS (
    SELECT
        fp.COMPANYCODE,
        MAX(CASE WHEN fp.end_date = DATE('2024-09-30') THEN fp.TOTALASSETTRATE END) AS turnover_2024q3,
        MAX(CASE WHEN fp.end_date = DATE('2025-09-30') THEN fp.TOTALASSETTRATE END) AS turnover_2025q3,
        MAX(CASE WHEN fp.end_date = DATE('2024-09-30') THEN fp.ROA END) AS roa_2024q3,
        MAX(CASE WHEN fp.end_date = DATE('2025-09-30') THEN fp.ROA END) AS roa_2025q3,
        COUNT(*) AS point_count
    FROM financial_points fp
    GROUP BY fp.COMPANYCODE
),
metrics AS (
    SELECT
        qp.COMPANYCODE,
        qp.turnover_2024q3,
        qp.turnover_2025q3,
        qp.roa_2024q3,
        qp.roa_2025q3
    FROM quarter_panel qp
    WHERE qp.point_count = 2
)
SELECT
    LPAD(c.SECUCODE, 6, '0') AS `股票代码`,
    c.company AS `公司简称`,
    c.industry AS `申万一级行业`,
    ROUND(m.turnover_2025q3 - m.turnover_2024q3, 4) AS `资产周转率改善幅度`,
    ROUND(m.roa_2025q3 - m.roa_2024q3, 4) AS `ROA改善幅度`
FROM candidate c
JOIN metrics m
  ON m.COMPANYCODE = c.COMPANYCODE
WHERE m.turnover_2025q3 > m.turnover_2024q3
  AND m.roa_2025q3 > m.roa_2024q3
ORDER BY
    `资产周转率改善幅度` DESC,
    `ROA改善幅度` DESC,
    `股票代码` ASC
LIMIT 23;
