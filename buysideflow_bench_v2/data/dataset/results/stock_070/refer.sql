WITH params AS (
    SELECT
        DATE('2025-04-30') AS as_of_date,
        TIMESTAMP('2025-04-30 23:59:59') AS as_of_dt,
        DATE('2025-03-31') AS report_end,
        DATE('2024-03-31') AS prior_report_end
),
trade_asof AS (
    SELECT MAX(t.TRADINGDATE) AS trade_date
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET IN (83, 90)
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
        FROM lc_liststatus ls
        JOIN params p
          ON ls.CHANGEDATE <= p.as_of_dt
         AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) <= p.as_of_dt
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
stock_base AS (
    SELECT
        t.INNERCODE,
        t.COMPANYCODE,
        t.SECUCODE,
        t.company,
        t.LISTEDDATE
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUCODE,
            COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS company,
            s.LISTEDDATE,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.SECUCODE, s.INNERCODE
            ) AS rn
        FROM secumain s
        JOIN params p
          ON s.LISTEDDATE <= p.as_of_date
        LEFT JOIN list_status_asof lsa
          ON lsa.INNERCODE = s.INNERCODE
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2, 6)
          AND lsa.change_type IN (1, 3)
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              WHERE ra.INNERCODE = s.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND DATE(ra.IMPLEMENTDATE) <= p.as_of_date
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= p.as_of_dt
                AND (
                    ra.REMOVEDATE IS NULL
                    OR DATE(ra.REMOVEDATE) > p.as_of_date
                    OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) > p.as_of_dt
                )
          )
    ) t
    WHERE t.rn = 1
),
candidate AS (
    SELECT sb.*
    FROM stock_base sb
    JOIN params p
      ON sb.LISTEDDATE <= DATE_SUB(p.as_of_date, INTERVAL 2 YEAR)
),
q1_revenue AS (
    SELECT
        m.COMPANYCODE,
        m.OPERATINGREENUE
    FROM (
        SELECT
            m.COMPANYCODE,
            m.ENDDATE,
            m.OPERATINGREENUE,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE, m.ENDDATE
                ORDER BY CASE m.MARK
                             WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                             WHEN 6 THEN 4 WHEN 2 THEN 5 ELSE 9
                         END,
                         m.INFOPUBLDATE DESC,
                         COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                         m.JSID DESC
            ) AS rn
        FROM lc_maindatanew m
        JOIN candidate c
          ON c.COMPANYCODE = m.COMPANYCODE
        JOIN params p
          ON m.ENDDATE = p.report_end
         AND m.INFOPUBLDATE <= p.as_of_dt
        WHERE m.ACCOUNTINGSTANDARDS = 1
          AND m.MARK IN (1, 8, 7, 6, 2)
          AND m.OPERATINGREENUE IS NOT NULL
    ) m
    WHERE m.rn = 1
),
prior_q1_revenue AS (
    SELECT
        m.COMPANYCODE,
        m.OPERATINGREENUE AS PRIOR_OPERATINGREENUE
    FROM (
        SELECT
            m.COMPANYCODE,
            m.ENDDATE,
            m.OPERATINGREENUE,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE, m.ENDDATE
                ORDER BY CASE m.MARK
                             WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                             WHEN 6 THEN 4 WHEN 2 THEN 5 ELSE 9
                         END,
                         m.INFOPUBLDATE DESC,
                         COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                         m.JSID DESC
            ) AS rn
        FROM lc_maindatanew m
        JOIN candidate c
          ON c.COMPANYCODE = m.COMPANYCODE
        JOIN params p
          ON m.ENDDATE = p.prior_report_end
         AND m.INFOPUBLDATE <= p.as_of_dt
        WHERE m.ACCOUNTINGSTANDARDS = 1
          AND m.MARK IN (1, 8, 7, 6, 2)
          AND m.OPERATINGREENUE IS NOT NULL
    ) m
    WHERE m.rn = 1
),
valuation_asof AS (
    SELECT
        v.COMPANYCODE,
        v.NEGOTIABLEMV
    FROM (
        SELECT
            c.COMPANYCODE,
            d.NEGOTIABLEMV,
            ROW_NUMBER() OVER (
                PARTITION BY c.COMPANYCODE
                ORDER BY d.JSID DESC
            ) AS rn
        FROM candidate c
        JOIN lc_dindicesforvaluation d
          ON d.INNERCODE = c.INNERCODE
        JOIN trade_asof ta
          ON d.TRADINGDAY = ta.trade_date
        JOIN params p
          ON COALESCE(d.INSERTTIME, d.TRADINGDAY) <= p.as_of_dt
        WHERE d.NEGOTIABLEMV IS NOT NULL
    ) v
    WHERE v.rn = 1
),
scored AS (
    SELECT
        c.SECUCODE,
        c.company,
        (r.OPERATINGREENUE / NULLIF(pr.PRIOR_OPERATINGREENUE, 0) - 1) * 100 AS OPERATINGREVENUEGROWRATE,
        r.OPERATINGREENUE,
        v.NEGOTIABLEMV
    FROM candidate c
    JOIN q1_revenue r
      ON r.COMPANYCODE = c.COMPANYCODE
    JOIN prior_q1_revenue pr
      ON pr.COMPANYCODE = c.COMPANYCODE
    JOIN valuation_asof v
      ON v.COMPANYCODE = c.COMPANYCODE
    WHERE r.OPERATINGREENUE >= 500000000
      AND pr.PRIOR_OPERATINGREENUE > 0
),
ranked AS (
    SELECT
        s.*,
        PERCENT_RANK() OVER (
            ORDER BY s.OPERATINGREVENUEGROWRATE DESC
        ) AS growth_pct_rank
    FROM scored s
)
SELECT
    SECUCODE AS `证券代码`,
    company AS `证券简称`,
    ROUND(OPERATINGREVENUEGROWRATE, 2) AS `2025Q1营收同比增速`,
    ROUND(OPERATINGREENUE / 100000000, 2) AS `2025Q1营收`,
    ROUND(NEGOTIABLEMV / 100000000, 2) AS `流通市值`
FROM ranked
WHERE growth_pct_rank <= 0.1
ORDER BY NEGOTIABLEMV ASC, `证券代码`
LIMIT 5;
