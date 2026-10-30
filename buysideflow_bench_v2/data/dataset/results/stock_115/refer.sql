WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
target_companies AS (
    SELECT DISTINCT
        COMPANYCODE,
        COALESCE(SECUABBR, CHINAMEABBR, CHINAME, SECUCODE) AS company_name
    FROM secumain
    WHERE SECUCATEGORY = 1
      AND SECUABBR IN ('万华化学', '华鲁恒升', '浙江龙盛')
),
quarter_roic AS (
    SELECT
        q.COMPANYCODE,
        q.ENDDATE,
        q.ROIC,
        ROW_NUMBER() OVER (
            PARTITION BY q.COMPANYCODE, q.ENDDATE
            ORDER BY
                CASE q.MARK
                    WHEN 1 THEN 1
                    WHEN 8 THEN 2
                    WHEN 7 THEN 3
                    WHEN 6 THEN 4
                    WHEN 2 THEN 5
                    ELSE 99
                END,
                q.INFOPUBLDATE DESC,
                q.INSERTTIME DESC,
                q.JSID DESC,
                q.ID DESC
        ) AS rn
    FROM lc_qfinancialindexnew q
    JOIN target_companies t
      ON t.COMPANYCODE = q.COMPANYCODE
    JOIN params p
      ON q.INFOPUBLDATE <= p.as_of_date
    WHERE q.MARK IN (1, 8, 7, 6, 2)
      AND q.ACCOUNTINGSTANDARDS = 1
      AND q.ENDDATE IN (DATE('2024-12-31'), DATE('2025-03-31'), DATE('2025-06-30'), DATE('2025-09-30'))
),
latest_tax AS (
    SELECT
        COMPANYCODE,
        effective_tax_rate
    FROM (
        SELECT
            m.COMPANYCODE,
            CASE
                WHEN m.TOTALPROFIT > 0 THEN m.INCOMETAXCOST / m.TOTALPROFIT * 100
                ELSE NULL
            END AS effective_tax_rate,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE, m.ENDDATE
                ORDER BY
                    CASE m.MARK
                        WHEN 1 THEN 1
                        WHEN 8 THEN 2
                        WHEN 7 THEN 3
                        WHEN 6 THEN 4
                        WHEN 2 THEN 5
                        ELSE 99
                    END,
                    m.INFOPUBLDATE DESC,
                    m.INSERTTIME DESC,
                    m.JSID DESC,
                    m.ID DESC
            ) AS rn
        FROM lc_maindatanew m
        JOIN target_companies t
          ON t.COMPANYCODE = m.COMPANYCODE
        JOIN params p
          ON m.INFOPUBLDATE <= p.as_of_date
        WHERE m.MARK IN (1, 8, 7, 6, 2)
          AND m.ACCOUNTINGSTANDARDS = 1
          AND m.ENDDATE = DATE('2024-12-31')
    ) x
    WHERE rn = 1
      AND effective_tax_rate IS NOT NULL
)
SELECT
    t.company_name AS `公司`,
    q.ENDDATE AS `报告期`,
    ROUND(q.ROIC, 4) AS `ROIC`,
    ROUND(lt.effective_tax_rate, 4) AS `最近年报有效税率`
FROM quarter_roic q
JOIN target_companies t
  ON t.COMPANYCODE = q.COMPANYCODE
LEFT JOIN latest_tax lt
  ON lt.COMPANYCODE = q.COMPANYCODE
WHERE q.rn = 1
  AND q.ROIC IS NOT NULL
ORDER BY t.company_name, q.ENDDATE DESC;
