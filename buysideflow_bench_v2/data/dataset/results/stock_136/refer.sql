WITH params AS (
    SELECT
        TIMESTAMP('2025-08-31 23:59:59') AS as_of_dt,
        DATE('2024-12-31') AS base_enddate,
        DATE('2025-06-30') AS target_enddate
),
target_company AS (
    SELECT DISTINCT s.COMPANYCODE
    FROM secumain s
    WHERE s.SECUCODE = '300442'
      AND s.SECUMARKET = 90
      AND s.SECUCATEGORY = 1
      AND s.COMPANYCODE IS NOT NULL
),
report_ranked AS (
    SELECT
        bs.COMPANYCODE,
        DATE(bs.ENDDATE) AS report_date,
        bs.TOTALFIXEDASSET,
        bs.TCONSTRUINPROCESS,
        ROW_NUMBER() OVER (
            PARTITION BY bs.COMPANYCODE, DATE(bs.ENDDATE)
            ORDER BY bs.INFOPUBLDATE DESC,
                     bs.INSERTTIME DESC,
                     bs.JSID DESC,
                     bs.ID DESC
        ) AS rn
    FROM lc_balancesheetall bs
    JOIN target_company tc
      ON tc.COMPANYCODE = bs.COMPANYCODE
    JOIN params p
      ON bs.ENDDATE IN (p.base_enddate, p.target_enddate)
     AND bs.INFOPUBLDATE <= p.as_of_dt
    WHERE bs.IFMERGED = 1
      AND bs.IFADJUSTED = 2
      AND bs.ACCOUNTINGSTANDARDS = 1
      AND bs.TOTALFIXEDASSET IS NOT NULL
      AND bs.TCONSTRUINPROCESS IS NOT NULL
),
reports AS (
    SELECT COMPANYCODE, report_date, TOTALFIXEDASSET, TCONSTRUINPROCESS
    FROM report_ranked
    WHERE rn = 1
),
comparison AS (
    SELECT
        COMPANYCODE,
        MAX(CASE WHEN report_date = DATE('2024-12-31') THEN TOTALFIXEDASSET END) AS fixed_assets_2024,
        MAX(CASE WHEN report_date = DATE('2025-06-30') THEN TOTALFIXEDASSET END) AS fixed_assets_2025h1,
        MAX(CASE WHEN report_date = DATE('2024-12-31') THEN TCONSTRUINPROCESS END) AS cip_2024,
        MAX(CASE WHEN report_date = DATE('2025-06-30') THEN TCONSTRUINPROCESS END) AS cip_2025h1
    FROM reports
    GROUP BY COMPANYCODE
    HAVING COUNT(*) = 2
)
SELECT
    ROUND(fixed_assets_2024 / 100000000, 2) AS fixed_assets_2024,
    ROUND(fixed_assets_2025h1 / 100000000, 2) AS fixed_assets_2025h1,
    ROUND(
        (fixed_assets_2025h1 - fixed_assets_2024) / NULLIF(fixed_assets_2024, 0) * 100,
        2
    ) AS fixed_assets_growth_pct,
    ROUND(cip_2024 / 100000000, 2) AS cip_2024,
    ROUND(cip_2025h1 / 100000000, 2) AS cip_2025h1,
    ROUND((cip_2025h1 - cip_2024) / 100000000, 2) AS cip_change
FROM comparison;
