WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
index_info AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '000906'
),
constituents AS (
    SELECT DISTINCT
        sm.COMPANYCODE,
        sm.INNERCODE,
        sm.SECUCODE,
        sm.LISTEDSECTOR,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, sm.SECUCODE) AS stock_name
    FROM lc_indexcomponent ic
    JOIN index_info ii
      ON ii.INNERCODE = ic.INDEXINNERCODE
    JOIN params p
      ON ic.INDATE <= p.as_of_date
    JOIN secumain sm
      ON sm.INNERCODE = ic.SECUINNERCODE
    WHERE (ic.OUTDATE IS NULL OR ic.OUTDATE > p.as_of_date)
      AND sm.SECUCATEGORY = 1
),
nonstib_annual AS (
    SELECT
        COMPANYCODE,
        report_year,
        revenue_growth,
        free_cash_flow
    FROM (
        SELECT
            m.COMPANYCODE,
            YEAR(m.ENDDATE) AS report_year,
            m.OPERATINGREVENUEGROWRATE AS revenue_growth,
            m.FREECASHFLOW AS free_cash_flow,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE, m.ENDDATE
                ORDER BY
                    m.INFOPUBLDATE DESC,
                    m.JSID DESC,
                    m.ID DESC
            ) AS rn
        FROM lc_mainindexnew m
        JOIN constituents c
          ON c.COMPANYCODE = m.COMPANYCODE
         AND c.LISTEDSECTOR <> 7
        JOIN params p
          ON m.INFOPUBLDATE <= p.as_of_date
        WHERE m.ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
    ) t
    WHERE rn = 1
),
stib_annual AS (
    SELECT
        COMPANYCODE,
        report_year,
        revenue_growth,
        free_cash_flow
    FROM (
        SELECT
            m.COMPANYCODE,
            YEAR(m.ENDDATE) AS report_year,
            m.OPERATINGREVENUEYOY AS revenue_growth,
            m.FREECASHFLOW AS free_cash_flow,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE, m.ENDDATE
                ORDER BY CASE WHEN m.IFADJUSTED = 1 THEN 0 ELSE 1 END,
                         m.INFOPUBLDATE DESC,
                         m.JSID DESC
            ) AS rn
        FROM lc_stibmainindex m
        JOIN constituents c
          ON c.COMPANYCODE = m.COMPANYCODE
         AND c.LISTEDSECTOR = 7
        JOIN params p
          ON m.INFOPUBLDATE <= p.as_of_date
        WHERE m.IFMERGED = 1
          AND m.ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
    ) t
    WHERE rn = 1
),
annual_pivot AS (
    SELECT
        COMPANYCODE,
        MAX(CASE WHEN report_year = 2024 THEN revenue_growth END) AS rev_growth_2024,
        MAX(CASE WHEN report_year = 2023 THEN revenue_growth END) AS rev_growth_2023,
        MAX(CASE WHEN report_year = 2024 THEN free_cash_flow END) AS fcf_2024,
        MAX(CASE WHEN report_year = 2023 THEN free_cash_flow END) AS fcf_2023,
        MAX(CASE WHEN report_year = 2022 THEN free_cash_flow END) AS fcf_2022
    FROM (
        SELECT * FROM nonstib_annual
        UNION ALL
        SELECT * FROM stib_annual
    ) a
    GROUP BY COMPANYCODE
),
quarterly AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        op_margin
    FROM (
        SELECT
            q.COMPANYCODE,
            q.ENDDATE,
            q.OPERATINGPROFITRATIO AS op_margin,
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
        JOIN constituents c
          ON c.COMPANYCODE = q.COMPANYCODE
        JOIN params p
          ON q.INFOPUBLDATE <= p.as_of_date
        WHERE q.MARK IN (1, 8, 7, 6, 2)
          AND q.ACCOUNTINGSTANDARDS = 1
          AND q.ENDDATE IN (DATE('2024-12-31'), DATE('2025-03-31'), DATE('2025-06-30'), DATE('2025-09-30'))
    ) t
    WHERE rn = 1
      AND op_margin IS NOT NULL
),
quarter_pivot AS (
    SELECT
        COMPANYCODE,
        MAX(CASE WHEN ENDDATE = DATE('2024-12-31') THEN op_margin END) AS op_margin_2024q4,
        MAX(CASE WHEN ENDDATE = DATE('2025-03-31') THEN op_margin END) AS op_margin_2025q1,
        MAX(CASE WHEN ENDDATE = DATE('2025-06-30') THEN op_margin END) AS op_margin_2025q2,
        MAX(CASE WHEN ENDDATE = DATE('2025-09-30') THEN op_margin END) AS op_margin_2025q3,
        COUNT(*) AS quarter_cnt
    FROM quarterly q
    GROUP BY COMPANYCODE
)
SELECT
    c.SECUCODE AS `股票代码`,
    c.stock_name AS `公司简称`,
    ROUND(a.rev_growth_2024, 4) AS `2024年营收增长率`,
    ROUND(a.rev_growth_2023, 4) AS `2023年营收增长率`,
    ROUND(a.fcf_2024 / 100000000, 2) AS `2024年自由现金流`,
    ROUND(a.fcf_2023 / 100000000, 2) AS `2023年自由现金流`,
    ROUND(a.fcf_2022 / 100000000, 2) AS `2022年自由现金流`,
    ROUND(q.op_margin_2024q4, 4) AS `2024Q4营业利润率`,
    ROUND(q.op_margin_2025q1, 4) AS `2025Q1营业利润率`,
    ROUND(q.op_margin_2025q2, 4) AS `2025Q2营业利润率`,
    ROUND(q.op_margin_2025q3, 4) AS `2025Q3营业利润率`
FROM constituents c
JOIN annual_pivot a
  ON a.COMPANYCODE = c.COMPANYCODE
JOIN quarter_pivot q
  ON q.COMPANYCODE = c.COMPANYCODE
WHERE a.rev_growth_2024 > a.rev_growth_2023
  AND a.rev_growth_2024 > 0
  AND a.rev_growth_2023 > 0
  AND a.fcf_2024 > 0
  AND a.fcf_2023 > 0
  AND a.fcf_2022 > 0
  AND q.quarter_cnt = 4
  AND q.op_margin_2024q4 >= 10
  AND q.op_margin_2025q1 >= 10
  AND q.op_margin_2025q2 >= 10
  AND q.op_margin_2025q3 >= 10
ORDER BY a.rev_growth_2024 DESC, c.SECUCODE;
