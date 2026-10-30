WITH params AS (
    SELECT DATE('2025-04-23') AS as_of_date
),
industry_asof AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY
                    e.INFOPUBLDATE DESC,
                    COALESCE(e.INSERTTIME, e.INFOPUBLDATE) DESC,
                    e.JSID DESC,
                    e.ID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND (e.INSERTTIME IS NULL OR e.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    ) t
    WHERE rn = 1
),
list_status_asof AS (
    SELECT INNERCODE
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY
                    ls.CHANGEDATE DESC,
                    COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
                    ls.JSID DESC,
                    ls.ID DESC
            ) AS rn
        FROM lc_liststatus ls
        JOIN params p
          ON ls.CHANGEDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
      AND CHANGETYPE IN (1, 3)
),
stock_base AS (
    SELECT COMPANYCODE, stock_code, stock_name
    FROM (
        SELECT
            s.COMPANYCODE,
            s.SECUCODE AS stock_code,
            COALESCE(NULLIF(s.SECUABBR, ''), NULLIF(s.CHINAMEABBR, ''), s.CHINAME) AS stock_name,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.SECUCODE
            ) AS rn
        FROM secumain s
        JOIN list_status_asof ls
          ON ls.INNERCODE = s.INNERCODE
        JOIN industry_asof i
          ON i.COMPANYCODE = s.COMPANYCODE
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2, 6)
          AND i.FIRSTINDUSTRYNAME NOT IN ('银行', '非银金融')
          AND s.COMPANYCODE IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              JOIN params p
                ON 1 = 1
              WHERE ra.INNERCODE = s.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND ra.IMPLEMENTDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE)
                    < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
                AND (
                    ra.REMOVEDATE IS NULL
                    OR ra.REMOVEDATE > p.as_of_date
                    OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.REMOVEDATE) > p.as_of_date
                )
          )
    ) t
    WHERE rn = 1
),
main_ranked AS (
    SELECT
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS report_date,
        m.CASHEQUIALENTS AS cash_balance,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
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
                COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                m.JSID DESC,
                m.ID DESC
        ) AS rn
    FROM lc_maindatanew m
    JOIN stock_base b
      ON b.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON DATE(m.ENDDATE) <= p.as_of_date
     AND m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
     AND (m.INSERTTIME IS NULL OR m.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
    WHERE m.MARK IN (1, 8, 7, 6, 2)
),
derivative_ranked AS (
    SELECT
        f.COMPANYCODE,
        DATE(f.ENDDATE) AS report_date,
        f.INBEARDEBTASSETSRATIO AS debt_ratio,
        f.INTERESTBEARDEBT AS interest_bearing_debt,
        ROW_NUMBER() OVER (
            PARTITION BY f.COMPANYCODE, DATE(f.ENDDATE)
            ORDER BY
                CASE f.MARK
                    WHEN 1 THEN 1
                    WHEN 8 THEN 2
                    WHEN 7 THEN 3
                    WHEN 6 THEN 4
                    WHEN 2 THEN 5
                    ELSE 99
                END,
                f.INFOPUBLDATE DESC,
                COALESCE(f.INSERTTIME, f.INFOPUBLDATE) DESC,
                f.JSID DESC,
                f.ID DESC
        ) AS rn
    FROM fin_derivative f
    JOIN stock_base b
      ON b.COMPANYCODE = f.COMPANYCODE
    JOIN params p
      ON DATE(f.ENDDATE) <= p.as_of_date
     AND f.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
     AND (f.INSERTTIME IS NULL OR f.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
    WHERE f.MARK IN (1, 8, 7, 6, 2)
),
main_periods AS (
    SELECT COMPANYCODE, report_date, cash_balance
    FROM main_ranked
    WHERE rn = 1
),
latest_period AS (
    SELECT COMPANYCODE, MAX(report_date) AS report_date
    FROM main_periods
    GROUP BY COMPANYCODE
),
financial_data AS (
    SELECT
        m.COMPANYCODE,
        m.report_date,
        m.cash_balance,
        d.debt_ratio,
        d.interest_bearing_debt,
        m.cash_balance - d.interest_bearing_debt AS net_cash
    FROM latest_period lp
    JOIN main_periods m
      ON m.COMPANYCODE = lp.COMPANYCODE
     AND m.report_date = lp.report_date
    JOIN derivative_ranked d
      ON d.COMPANYCODE = m.COMPANYCODE
     AND d.report_date = m.report_date
     AND d.rn = 1
)
SELECT
    b.stock_code AS 证券代码,
    b.stock_name AS 证券简称,
    DATE_FORMAT(f.report_date, '%Y-%m-%d') AS 报告期,
    ROUND(f.debt_ratio, 2) AS 有息负债率,
    ROUND(f.cash_balance / 100000000, 2) AS 货币资金,
    ROUND(f.interest_bearing_debt / 100000000, 2) AS 有息债务,
    ROUND(f.net_cash / 100000000, 2) AS 净现金额
FROM financial_data f
JOIN stock_base b
  ON b.COMPANYCODE = f.COMPANYCODE
WHERE f.debt_ratio >= 0
  AND f.debt_ratio < 10
  AND f.cash_balance >= 0
  AND f.interest_bearing_debt >= 0
  AND f.cash_balance > f.interest_bearing_debt
ORDER BY f.net_cash DESC, b.stock_code ASC
LIMIT 10;
