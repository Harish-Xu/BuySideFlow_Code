WITH params AS (
    SELECT DATE('2025-04-28') AS as_of_date
),
company_a AS (
    SELECT
        l.ENDDATE,
        l.GROSSINCOMERATIO,
        l.NETPROFITRATIO,
        ROW_NUMBER() OVER (
            PARTITION BY l.COMPANYCODE, l.ENDDATE
            ORDER BY l.INFOPUBLDATE DESC, l.INSERTTIME DESC, l.JSID DESC
        ) AS rn
    FROM lc_mainindexnew l
    JOIN secumain s
      ON s.COMPANYCODE = l.COMPANYCODE
    JOIN params p
      ON l.INFOPUBLDATE <= p.as_of_date
    WHERE s.SECUCODE = '603288'
      AND s.SECUCATEGORY = 1
      AND s.SECUMARKET = 83
      AND MONTH(l.ENDDATE) = 12
      AND l.GROSSINCOMERATIO IS NOT NULL
      AND l.NETPROFITRATIO IS NOT NULL
),
company_b AS (
    SELECT
        l.ENDDATE,
        l.GROSSINCOMERATIO,
        l.NETPROFITRATIO,
        ROW_NUMBER() OVER (
            PARTITION BY l.COMPANYCODE, l.ENDDATE
            ORDER BY l.INFOPUBLDATE DESC, l.INSERTTIME DESC, l.JSID DESC
        ) AS rn
    FROM lc_mainindexnew l
    JOIN secumain s
      ON s.COMPANYCODE = l.COMPANYCODE
    JOIN params p
      ON l.INFOPUBLDATE <= p.as_of_date
    WHERE s.SECUCODE = '603027'
      AND s.SECUCATEGORY = 1
      AND s.SECUMARKET = 83
      AND MONTH(l.ENDDATE) = 12
      AND l.GROSSINCOMERATIO IS NOT NULL
      AND l.NETPROFITRATIO IS NOT NULL
),
annual_joined AS (
    SELECT
        a.ENDDATE AS report_date,
        a.GROSSINCOMERATIO AS company1_gross_ratio,
        a.NETPROFITRATIO AS company1_net_ratio,
        b.GROSSINCOMERATIO AS company2_gross_ratio,
        b.NETPROFITRATIO AS company2_net_ratio
    FROM company_a a
    JOIN company_b b
      ON a.ENDDATE = b.ENDDATE
    WHERE a.rn = 1
      AND b.rn = 1
),
latest_five_reports AS (
    SELECT *
    FROM annual_joined
    ORDER BY report_date DESC
    LIMIT 5
)
SELECT
    report_date,
    '海天味业' AS company1_abbr,
    '千禾味业' AS company2_abbr,
    ROUND(company1_gross_ratio, 4) AS company1_gross_ratio,
    ROUND(company1_net_ratio, 4) AS company1_net_ratio,
    ROUND(company2_gross_ratio, 4) AS company2_gross_ratio,
    ROUND(company2_net_ratio, 4) AS company2_net_ratio,
    ROUND(company1_gross_ratio - company2_gross_ratio, 4) AS gross_ratio_diff,
    ROUND(company1_net_ratio - company2_net_ratio, 4) AS net_ratio_diff
FROM latest_five_reports
ORDER BY report_date DESC;
