WITH
params AS (
    SELECT
        DATE('2025-11-14') AS as_of_date,
        DATE('2025-09-30') AS current_end_date,
        DATE('2024-09-30') AS prior_end_date
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
                    e.INSERTTIME DESC,
                    e.JSID DESC,
                    e.ID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND e.FIRSTINDUSTRYNAME IS NOT NULL
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    ) t
    WHERE rn = 1
),
list_status_asof AS (
    SELECT INNERCODE, CHANGETYPE
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY
                    ls.CHANGEDATE DESC,
                    ls.INSERTTIME DESC,
                    ls.JSID DESC,
                    ls.ID DESC
            ) AS rn
        FROM lc_liststatus ls
        JOIN params p
          ON ls.CHANGEDATE <= p.as_of_date
         AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
stock_pool AS (
    SELECT INNERCODE, COMPANYCODE, stock_code, stock_name, FIRSTINDUSTRYNAME
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            LPAD(s.SECUCODE, 6, '0') AS stock_code,
            COALESCE(NULLIF(s.SECUABBR, ''), NULLIF(s.CHINAMEABBR, ''), s.CHINAME) AS stock_name,
            ia.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.SECUMARKET, s.SECUCODE, s.INNERCODE
            ) AS rn
        FROM secumain s
        JOIN params p
          ON s.LISTEDDATE <= p.as_of_date
        JOIN list_status_asof lsa
          ON lsa.INNERCODE = s.INNERCODE
         AND lsa.CHANGETYPE IN (1, 3)
        JOIN industry_asof ia
          ON ia.COMPANYCODE = s.COMPANYCODE
         AND ia.FIRSTINDUSTRYNAME NOT IN ('银行', '非银金融')
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2, 6)
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              WHERE ra.INNERCODE = s.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND DATE(ra.IMPLEMENTDATE) <= p.as_of_date
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
                AND (
                    ra.REMOVEDATE IS NULL
                    OR DATE(ra.REMOVEDATE) > p.as_of_date
                    OR DATE(COALESCE(ra.REMOVEINFOPUBLDATE, ra.REMOVEDATE)) > p.as_of_date
                )
          )
    ) t
    WHERE rn = 1
),
-- Bind both periods to the latest available 2025 Q3 disclosure.
-- Source codes: 110104 = third-quarter report; 120105 = corrected Q3 report.
-- A prior-period MARK=1 row is the comparative published with that report.
report_rows AS (
    SELECT m.*
    FROM lc_maindatanew m
    JOIN stock_pool sp ON sp.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.ENDDATE IN (p.current_end_date, p.prior_end_date)
     AND m.INFOPUBLDATE >= p.current_end_date
     AND m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE m.INFOSOURCECODE IN (110104, 120105)
      AND m.MARK IN (1, 2)
),
current_report_ranked AS (
    SELECT
        m.*,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE
            ORDER BY m.INFOPUBLDATE DESC,
                     CASE m.MARK WHEN 1 THEN 0 ELSE 1 END,
                     m.INSERTTIME DESC, m.JSID DESC, m.ID DESC
        ) AS report_rn
    FROM report_rows m
    JOIN params p ON m.ENDDATE = p.current_end_date
),
current_report AS (
    SELECT * FROM current_report_ranked WHERE report_rn = 1
),
financial_ranked AS (
    SELECT
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS end_date,
        -- Use the disclosed ROE; do not synthesize an undisclosed value from
        -- a derivative row (e.g. when weighted net assets are non-positive).
        m.WROE,
        f.TOTALASSETTRATE,
        f.DEBTASSETSRATIO,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
            ORDER BY m.INSERTTIME DESC, m.JSID DESC, m.ID DESC,
                     f.INSERTTIME DESC, f.JSID DESC, f.ID DESC
        ) AS rn
    FROM report_rows m
    JOIN current_report r
      ON r.COMPANYCODE = m.COMPANYCODE
     AND r.INFOPUBLDATE = m.INFOPUBLDATE
    JOIN params p
      ON (m.ENDDATE = p.current_end_date AND m.ID = r.ID)
      OR (m.ENDDATE = p.prior_end_date AND m.MARK = 1)
    LEFT JOIN fin_derivative f
      ON f.COMPANYCODE = m.COMPANYCODE
     AND f.ENDDATE = m.ENDDATE
     AND f.INFOPUBLDATE = m.INFOPUBLDATE
     AND f.MARK = m.MARK
     AND f.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
),
financial_panel AS (
    SELECT COMPANYCODE, end_date, WROE, TOTALASSETTRATE, DEBTASSETSRATIO
    FROM financial_ranked
    WHERE rn = 1
),
comparison AS (
    SELECT
        fp.COMPANYCODE,
        MAX(CASE WHEN fp.end_date = p.current_end_date THEN fp.WROE END) AS current_roe,
        MAX(CASE WHEN fp.end_date = p.prior_end_date THEN fp.WROE END) AS prior_roe,
        MAX(CASE WHEN fp.end_date = p.current_end_date THEN fp.TOTALASSETTRATE END) AS current_turnover,
        MAX(CASE WHEN fp.end_date = p.prior_end_date THEN fp.TOTALASSETTRATE END) AS prior_turnover,
        MAX(CASE WHEN fp.end_date = p.current_end_date THEN fp.DEBTASSETSRATIO END) AS current_debt_ratio,
        MAX(CASE WHEN fp.end_date = p.prior_end_date THEN fp.DEBTASSETSRATIO END) AS prior_debt_ratio
    FROM financial_panel fp
    JOIN params p
      ON 1 = 1
    GROUP BY fp.COMPANYCODE
    HAVING current_roe IS NOT NULL
       AND prior_roe IS NOT NULL
       AND current_turnover IS NOT NULL
       AND prior_turnover IS NOT NULL
       AND current_debt_ratio IS NOT NULL
       AND prior_debt_ratio IS NOT NULL
),
qualified AS (
    SELECT
        sp.stock_code,
        sp.stock_name,
        sp.FIRSTINDUSTRYNAME,
        c.current_roe,
        c.current_roe - c.prior_roe AS roe_increase,
        c.current_turnover - c.prior_turnover AS turnover_increase,
        c.current_debt_ratio - c.prior_debt_ratio AS debt_ratio_change
    FROM comparison c
    JOIN stock_pool sp
      ON sp.COMPANYCODE = c.COMPANYCODE
    WHERE c.current_roe > 12
      AND c.current_roe > c.prior_roe
      AND c.current_turnover > c.prior_turnover
      AND c.current_debt_ratio <= c.prior_debt_ratio
)
SELECT
    stock_code AS `股票代码`,
    stock_name AS `证券名称`,
    FIRSTINDUSTRYNAME AS `行业`,
    ROUND(current_roe, 2) AS `加权ROE`,
    ROUND(roe_increase, 2) AS `加权ROE同比提升`,
    ROUND(turnover_increase, 2) AS `总资产周转率同比提升`,
    ROUND(debt_ratio_change, 2) AS `资产负债率同比变化`
FROM qualified
ORDER BY roe_increase DESC, stock_code ASC
LIMIT 20;
