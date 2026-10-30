WITH
params AS (
    SELECT
        DATE('2025-12-31') AS as_of_date,
        DATE('2025-09-30') AS latest_financial_end
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
         AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE ls.SECUMARKET IN (83, 90)
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
      AND s.SECUMARKET IN (83, 90)
      AND la.CHANGETYPE IN (1, 3)
    GROUP BY s.COMPANYCODE
),
stock_base AS (
    SELECT
        s.COMPANYCODE,
        s.INNERCODE,
        s.LISTEDSECTOR,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS company
    FROM stock_seed ss
    JOIN secumain s
      ON s.INNERCODE = ss.INNERCODE
),
industry_asof AS (
    SELECT
        COMPANYCODE,
        SECONDINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.SECONDINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR <> 7
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(e.INSERTTIME, e.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
          AND e.FIRSTINDUSTRYNAME = '医药生物'
        UNION ALL
        SELECT
            e.COMPANYCODE,
            e.SECONDINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
            ) AS rn
        FROM lc_stibexgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR = 7
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(e.INSERTTIME, e.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND e.EFFECTIVEDATE <= p.as_of_date
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
          AND e.FIRSTINDUSTRYNAME = '医药生物'
    ) t
    WHERE rn = 1
),
candidate AS (
    SELECT
        sb.COMPANYCODE,
        sb.SECUCODE,
        sb.company,
        sb.LISTEDSECTOR,
        ia.SECONDINDUSTRYNAME
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
),
latest_balance AS (
    SELECT COMPANYCODE, cash_to_current_liability
    FROM (
    SELECT
        m.COMPANYCODE,
        m.ENDPERIODCASHEQUIVALENT / NULLIF(m.TOTALCURRENTLIABILITY, 0) AS cash_to_current_liability,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE
            ORDER BY CASE m.MARK
                         WHEN 1 THEN 0 WHEN 8 THEN 1 WHEN 7 THEN 2
                         WHEN 6 THEN 3 WHEN 2 THEN 4 ELSE 9
                     END,
                     m.INFOPUBLDATE DESC,
                     COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                     m.JSID DESC
        ) AS rn
    FROM lc_maindatanew m
    JOIN candidate c
      ON c.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
     AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE m.ENDDATE = p.latest_financial_end
      AND m.MARK IN (1, 8, 7, 6, 2)
      AND m.ACCOUNTINGSTANDARDS = 1
      AND m.ENDPERIODCASHEQUIVALENT IS NOT NULL
      AND m.TOTALCURRENTLIABILITY IS NOT NULL
    ) t
    WHERE rn = 1
),
latest_interest_cover AS (
    SELECT COMPANYCODE, INTERESTCOVER
    FROM (
        SELECT
            f.COMPANYCODE,
            f.INTERESTCOVER,
            ROW_NUMBER() OVER (
                PARTITION BY f.COMPANYCODE
                ORDER BY CASE f.MARK
                             WHEN 1 THEN 0 WHEN 8 THEN 1 WHEN 7 THEN 2
                             WHEN 6 THEN 3 WHEN 2 THEN 4 ELSE 9
                         END,
                         f.INFOPUBLDATE DESC,
                         COALESCE(f.INSERTTIME, f.INFOPUBLDATE) DESC,
                         f.JSID DESC
            ) AS rn
        FROM fin_derivative f
        JOIN candidate c
          ON c.COMPANYCODE = f.COMPANYCODE
        JOIN params p
          ON f.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(f.INSERTTIME, f.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE f.ENDDATE = p.latest_financial_end
          AND f.MARK IN (1, 8, 7, 6, 2)
          AND f.INTERESTCOVER IS NOT NULL
    ) t
    WHERE rn = 1
),
scored AS (
    SELECT
        c.SECONDINDUSTRYNAME AS sub_industry,
        lb.cash_to_current_liability,
        li.INTERESTCOVER AS interest_cover
    FROM candidate c
    JOIN latest_balance lb
      ON lb.COMPANYCODE = c.COMPANYCODE
    JOIN latest_interest_cover li
      ON li.COMPANYCODE = c.COMPANYCODE
    WHERE lb.cash_to_current_liability IS NOT NULL
      AND li.INTERESTCOVER IS NOT NULL
),
industry_stats AS (
    SELECT
        sub_industry,
        COUNT(*) AS comparable_count,
        SUM(CASE WHEN cash_to_current_liability > 0.5 AND interest_cover > 5 THEN 1 ELSE 0 END) AS high_cash_strong_count,
        SUM(CASE WHEN cash_to_current_liability > 0.5 AND interest_cover <= 5 THEN 1 ELSE 0 END) AS high_cash_general_count,
        SUM(CASE WHEN cash_to_current_liability <= 0.5 AND interest_cover > 5 THEN 1 ELSE 0 END) AS general_cash_strong_count,
        SUM(CASE WHEN cash_to_current_liability <= 0.5 AND interest_cover <= 5 THEN 1 ELSE 0 END) AS weak_count
    FROM scored
    GROUP BY sub_industry
)
SELECT
    s.sub_industry AS `申万二级行业`,
    s.comparable_count AS `可比公司数`,
    ROUND(s.high_cash_strong_count / NULLIF(s.comparable_count, 0) * 100, 2) AS `高现金强保障占比`,
    ROUND(s.high_cash_general_count / NULLIF(s.comparable_count, 0) * 100, 2) AS `高现金保障一般占比`,
    ROUND(s.general_cash_strong_count / NULLIF(s.comparable_count, 0) * 100, 2) AS `现金一般保障较强占比`,
    ROUND(s.weak_count / NULLIF(s.comparable_count, 0) * 100, 2) AS `双弱占比`
FROM industry_stats s
ORDER BY s.sub_industry;
