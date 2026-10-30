WITH
params AS (
    SELECT
        DATE('2025-04-25') AS as_of_date
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
        FROM lc_liststatus ls
        JOIN params p
          ON ls.CHANGEDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND ls.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
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
    LEFT JOIN listed_asof la
      ON la.INNERCODE = s.INNERCODE
    JOIN params p
      ON s.LISTEDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR IN (1, 2, 6)
      AND la.CHANGETYPE IN (1, 2, 3, 6)
      AND NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = s.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND DATE(ra.IMPLEMENTDATE) <= p.as_of_date
            AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
            AND (
                ra.REMOVEDATE IS NULL
                OR DATE(ra.REMOVEDATE) > p.as_of_date
                OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.REMOVEDATE) >= DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
            )
      )
    GROUP BY s.COMPANYCODE
),
stock_base AS (
    SELECT
        s.COMPANYCODE,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS company
    FROM stock_seed ss
    JOIN secumain s
      ON s.INNERCODE = ss.INNERCODE
),
industry_asof AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC, e.ID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    ) t
    WHERE rn = 1
),
candidate AS (
    SELECT sb.COMPANYCODE, sb.SECUCODE, sb.company, ia.FIRSTINDUSTRYNAME
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    WHERE ia.FIRSTINDUSTRYNAME IN ('建筑装饰', '机械设备')
),
balance_version_ranked AS (
    SELECT
        b.COMPANYCODE,
        DATE(b.ENDDATE) AS end_date,
        b.OTHERRECEIVABLEED,
        b.OTHERPAYABLEED,
        b.TOTALASSETS,
        ROW_NUMBER() OVER (
            PARTITION BY b.COMPANYCODE, DATE(b.ENDDATE)
            ORDER BY b.INFOPUBLDATE DESC,
                     b.INSERTTIME DESC,
                     b.JSID DESC,
                     b.ID DESC
        ) AS version_rn
    FROM lc_balancesheetall b
    JOIN candidate c
      ON c.COMPANYCODE = b.COMPANYCODE
    JOIN params p
      ON b.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE b.ENDDATE <= p.as_of_date
      AND b.IFMERGED = 1
      AND b.ACCOUNTINGSTANDARDS = 1
),
balance_latest_period AS (
    SELECT
        bv.*,
        ROW_NUMBER() OVER (
            PARTITION BY bv.COMPANYCODE
            ORDER BY bv.end_date DESC
        ) AS period_rn
    FROM balance_version_ranked bv
    WHERE bv.version_rn = 1
),
scored AS (
    SELECT
        c.FIRSTINDUSTRYNAME AS industry,
        c.SECUCODE AS code,
        b.end_date,
        b.OTHERRECEIVABLEED / b.TOTALASSETS AS other_receivable_ratio,
        b.OTHERPAYABLEED / b.TOTALASSETS AS other_payable_ratio,
        GREATEST(
            b.OTHERRECEIVABLEED / b.TOTALASSETS,
            b.OTHERPAYABLEED / b.TOTALASSETS
        ) AS concentration_ratio
    FROM balance_latest_period b
    JOIN candidate c
      ON c.COMPANYCODE = b.COMPANYCODE
    WHERE b.period_rn = 1
      AND b.TOTALASSETS > 0
      -- Choose the latest visible report first; never fill missing values from an older one.
      AND b.OTHERRECEIVABLEED IS NOT NULL
      AND b.OTHERPAYABLEED IS NOT NULL
),
ranked AS (
    SELECT
        s.*,
        ROW_NUMBER() OVER (
            PARTITION BY s.industry
            ORDER BY s.concentration_ratio DESC, s.code
        ) AS industry_rank
    FROM scored s
)
SELECT
    industry AS `行业`,
    code AS `代码`,
    DATE_FORMAT(end_date, '%Y-%m-%d') AS `报告期末日期`,
    ROUND(other_receivable_ratio * 100, 2) AS `其他应收占比`,
    ROUND(other_payable_ratio * 100, 2) AS `其他应付占比`
FROM ranked
WHERE industry_rank <= 2
ORDER BY industry, concentration_ratio DESC, code;
