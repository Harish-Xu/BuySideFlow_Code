WITH params AS (
    SELECT
        DATE('2025-12-31') AS as_of_date,
        DATE('2025-06-30') AS target_end,
        DATE('2024-06-30') AS compare_end
),
target_stocks AS (
    SELECT COMPANYCODE, SECUCODE, company_name
    FROM (
        SELECT
            s.COMPANYCODE,
            s.SECUCODE,
            COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS company_name,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.INNERCODE
            ) AS rn
        FROM secumain s
        WHERE s.SECUCATEGORY = 1
          AND s.SECUCODE IN ('300308', '300502', '300394', '002281', '603083', '002902')
    ) t
    WHERE rn = 1
),
mainindex_ranked AS (
    SELECT
        m.COMPANYCODE,
        m.ENDDATE,
        m.OPERATINGREVENUEGROWRATE,
        m.NPPARENTCOMPANYYOY,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn
    FROM lc_mainindexnew m
    JOIN target_stocks ts
      ON ts.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.ENDDATE IN (p.target_end, p.compare_end)
     AND m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
),
mainindex AS (
    SELECT *
    FROM mainindex_ranked
    WHERE rn = 1
      AND OPERATINGREVENUEGROWRATE IS NOT NULL
      AND NPPARENTCOMPANYYOY IS NOT NULL
),
derivative_ranked AS (
    SELECT
        f.COMPANYCODE,
        f.ENDDATE,
        f.GROSSINCOMERATIO,
        f.NETPROFITRATIO,
        f.PERIODEXPENSE,
        ROW_NUMBER() OVER (
            PARTITION BY f.COMPANYCODE, f.ENDDATE
            ORDER BY CASE f.MARK
                         WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                         WHEN 6 THEN 4 WHEN 2 THEN 5 ELSE 99 END,
                     f.INFOPUBLDATE DESC,
                     COALESCE(f.INSERTTIME, f.INFOPUBLDATE) DESC,
                     f.ID DESC
        ) AS rn
    FROM fin_derivative f
    JOIN target_stocks ts
      ON ts.COMPANYCODE = f.COMPANYCODE
    JOIN params p
      ON f.ENDDATE IN (p.target_end, p.compare_end)
     AND f.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE f.MARK IN (1, 8, 7, 6, 2)
),
derivative_data AS (
    SELECT *
    FROM derivative_ranked
    WHERE rn = 1
      AND GROSSINCOMERATIO IS NOT NULL
      AND NETPROFITRATIO IS NOT NULL
      AND PERIODEXPENSE IS NOT NULL
),
maindata_ranked AS (
    SELECT
        d.COMPANYCODE,
        d.ENDDATE,
        d.OPERATINGREENUE,
        ROW_NUMBER() OVER (
            PARTITION BY d.COMPANYCODE, d.ENDDATE
            ORDER BY CASE d.MARK
                         WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                         WHEN 6 THEN 4 WHEN 2 THEN 5 ELSE 99 END,
                     d.INFOPUBLDATE DESC,
                     d.JSID DESC
        ) AS rn
    FROM lc_maindatanew d
    JOIN target_stocks ts
      ON ts.COMPANYCODE = d.COMPANYCODE
    JOIN params p
      ON d.ENDDATE IN (p.target_end, p.compare_end)
     AND d.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE d.MARK IN (1, 8, 7, 6, 2)
),
maindata AS (
    SELECT *
    FROM maindata_ranked
    WHERE rn = 1
      AND OPERATINGREENUE IS NOT NULL
      AND OPERATINGREENUE <> 0
),
pivoted AS (
    SELECT
        ts.SECUCODE,
        ts.company_name,
        MAX(CASE WHEN mi.ENDDATE = p.target_end THEN mi.OPERATINGREVENUEGROWRATE END) AS revenue_yoy,
        MAX(CASE WHEN mi.ENDDATE = p.target_end THEN mi.NPPARENTCOMPANYYOY END) AS parent_np_yoy,
        MAX(CASE WHEN fd.ENDDATE = p.target_end THEN fd.GROSSINCOMERATIO END) AS gross_margin,
        MAX(CASE WHEN fd.ENDDATE = p.target_end THEN fd.NETPROFITRATIO END) AS net_margin,
        MAX(CASE WHEN fd.ENDDATE = p.target_end THEN
            100 * fd.PERIODEXPENSE / md.OPERATINGREENUE
        END) AS expense_rate_2025h1,
        MAX(CASE WHEN fd.ENDDATE = p.compare_end THEN
            100 * fd.PERIODEXPENSE / md.OPERATINGREENUE
        END) AS expense_rate_2024h1
    FROM target_stocks ts
    CROSS JOIN params p
    JOIN mainindex mi
      ON mi.COMPANYCODE = ts.COMPANYCODE
    JOIN derivative_data fd
      ON fd.COMPANYCODE = ts.COMPANYCODE
     AND fd.ENDDATE = mi.ENDDATE
    JOIN maindata md
      ON md.COMPANYCODE = ts.COMPANYCODE
     AND md.ENDDATE = mi.ENDDATE
    GROUP BY ts.SECUCODE, ts.company_name
)
SELECT
    SECUCODE AS `股票代码`,
    company_name AS `股票简称`,
    ROUND(revenue_yoy, 4) AS `营业收入同比(%)`,
    ROUND(parent_np_yoy, 4) AS `归母净利润同比(%)`,
    ROUND(gross_margin, 4) AS `毛利率(%)`,
    ROUND(net_margin, 4) AS `净利率(%)`,
    ROUND(expense_rate_2025h1, 4) AS `期间费用率(%)`,
    ROUND(expense_rate_2025h1 - expense_rate_2024h1, 4) AS `期间费用率同比变化(百分点)`,
    ROUND(parent_np_yoy - revenue_yoy, 4) AS `利润兑现效率(百分点)`
FROM pivoted
WHERE revenue_yoy IS NOT NULL
  AND parent_np_yoy IS NOT NULL
  AND gross_margin IS NOT NULL
  AND net_margin IS NOT NULL
  AND expense_rate_2025h1 IS NOT NULL
  AND expense_rate_2024h1 IS NOT NULL
ORDER BY (parent_np_yoy - revenue_yoy) DESC, `股票代码`;
