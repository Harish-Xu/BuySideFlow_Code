WITH
params AS (
    SELECT
        DATE('2025-11-21') AS as_of_date,
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
        WHERE ls.SECUMARKET IN (83, 90, 18)
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
      AND s.SECUMARKET IN (83, 90, 18)
      AND la.CHANGETYPE IN (1, 3)
    GROUP BY s.COMPANYCODE
),
stock_base AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        s.SECUCODE,
        s.LISTEDSECTOR,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS company
    FROM stock_seed ss
    JOIN secumain s
      ON s.INNERCODE = ss.INNERCODE
),
industry_asof AS (
    SELECT
        COMPANYCODE,
        FIRSTINDUSTRYNAME AS industry
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC,
                         e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR <> 7
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_date
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
          AND e.FIRSTINDUSTRYNAME IN ('石油石化', '基础化工')
        UNION ALL
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC,
                         e.JSID DESC
            ) AS rn
        FROM lc_stibexgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR = 7
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_date
        WHERE e.STANDARD = 38
          AND e.EFFECTIVEDATE <= p.as_of_date
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
          AND e.FIRSTINDUSTRYNAME IN ('石油石化', '基础化工')
    ) t
    WHERE rn = 1
),
candidate AS (
    SELECT
        sb.COMPANYCODE,
        sb.SECUCODE,
        sb.company,
        sb.LISTEDSECTOR,
        ia.industry
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
),
quarterly_gm_ranked AS (
    SELECT
        q.COMPANYCODE,
        q.ENDDATE,
        q.GROSSINCOMERATIO,
        ROW_NUMBER() OVER (
            PARTITION BY q.COMPANYCODE, q.ENDDATE
            ORDER BY CASE q.MARK
                         WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                         WHEN 6 THEN 4 WHEN 2 THEN 5 ELSE 9
                     END,
                     q.INFOPUBLDATE DESC,
                     COALESCE(q.INSERTTIME, q.INFOPUBLDATE) DESC,
                     q.JSID DESC,
                     q.ID DESC
        ) AS rn
    FROM lc_qfinancialindexnew q
    JOIN candidate c
      ON c.COMPANYCODE = q.COMPANYCODE
    JOIN params p
      ON q.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE q.MARK IN (1, 8, 7, 6, 2)
      AND q.ACCOUNTINGSTANDARDS = 1
      AND q.ENDDATE IN (
          DATE('2024-12-31'),
          DATE('2025-03-31'),
          DATE('2025-06-30'),
          DATE('2025-09-30')
      )
      AND q.GROSSINCOMERATIO IS NOT NULL
),
gm_4q AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        GROSSINCOMERATIO
    FROM quarterly_gm_ranked
    WHERE rn = 1
),
gross_margin_4q AS (
    SELECT
        COMPANYCODE,
        MAX(CASE WHEN ENDDATE = DATE('2025-09-30') THEN GROSSINCOMERATIO END) AS latest_gross_margin,
        STDDEV_POP(GROSSINCOMERATIO) AS gross_margin_std_4q,
        COUNT(*) AS quarter_cnt
    FROM gm_4q
    GROUP BY COMPANYCODE
),
nonstib_latest_roe AS (
    SELECT
        COMPANYCODE,
        ROETTM
    FROM (
        SELECT
            m.COMPANYCODE,
            m.ROETTM,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE
                ORDER BY m.ENDDATE DESC,
                         m.INFOPUBLDATE DESC,
                         COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                         m.JSID DESC
            ) AS rn
        FROM lc_mainindexnew m
        JOIN candidate c
          ON c.COMPANYCODE = m.COMPANYCODE
         AND c.LISTEDSECTOR <> 7
        JOIN params p
          ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE m.ENDDATE <= p.latest_financial_end
          AND m.ROETTM IS NOT NULL
    ) t
    WHERE rn = 1
),
stib_latest_roe AS (
    SELECT
        COMPANYCODE,
        ROETTM
    FROM (
        SELECT
            m.COMPANYCODE,
            m.ROETTM,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE
                ORDER BY m.ENDDATE DESC,
                         CASE WHEN m.IFADJUSTED = 1 THEN 0 ELSE 1 END,
                         m.INFOPUBLDATE DESC,
                         COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                         m.JSID DESC
            ) AS rn
        FROM lc_stibmainindex m
        JOIN candidate c
          ON c.COMPANYCODE = m.COMPANYCODE
         AND c.LISTEDSECTOR = 7
        JOIN params p
          ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE m.IFMERGED = 1
          AND m.ENDDATE <= p.latest_financial_end
          AND m.ROETTM IS NOT NULL
    ) t
    WHERE rn = 1
),
latest_roe AS (
    SELECT * FROM nonstib_latest_roe
    UNION ALL
    SELECT * FROM stib_latest_roe
),
scored AS (
    SELECT
        c.industry,
        gm.latest_gross_margin,
        gm.gross_margin_std_4q,
        lr.ROETTM,
        CASE
            WHEN gm.quarter_cnt = 4
             AND gm.gross_margin_std_4q < 3
             AND lr.ROETTM > 10
            THEN 1 ELSE 0
        END AS hit_flag
    FROM candidate c
    JOIN gross_margin_4q gm
      ON gm.COMPANYCODE = c.COMPANYCODE
    JOIN latest_roe lr
      ON lr.COMPANYCODE = c.COMPANYCODE
    WHERE gm.quarter_cnt = 4
),
hit_sample AS (
    SELECT *
    FROM scored
    WHERE hit_flag = 1
),
gm_ranked AS (
    SELECT
        industry,
        latest_gross_margin,
        ROW_NUMBER() OVER (PARTITION BY industry ORDER BY latest_gross_margin) AS rn,
        COUNT(*) OVER (PARTITION BY industry) AS cnt
    FROM hit_sample
),
gm_median AS (
    SELECT
        industry,
        AVG(latest_gross_margin) AS latest_gross_margin_median
    FROM gm_ranked
    WHERE rn IN (FLOOR((cnt + 1) / 2), FLOOR((cnt + 2) / 2))
    GROUP BY industry
),
roe_ranked AS (
    SELECT
        industry,
        ROETTM,
        ROW_NUMBER() OVER (PARTITION BY industry ORDER BY ROETTM) AS rn,
        COUNT(*) OVER (PARTITION BY industry) AS cnt
    FROM hit_sample
),
roe_median AS (
    SELECT
        industry,
        AVG(ROETTM) AS roe_ttm_median
    FROM roe_ranked
    WHERE rn IN (FLOOR((cnt + 1) / 2), FLOOR((cnt + 2) / 2))
    GROUP BY industry
)
SELECT
    s.industry AS `行业`,
    CAST(SUM(s.hit_flag) AS UNSIGNED) AS `满足数`,
    COUNT(*) AS `行业样本总数`,
    ROUND(SUM(s.hit_flag) / NULLIF(COUNT(*), 0) * 100, 2) AS `占比`,
    ROUND(gm.latest_gross_margin_median, 4) AS `最新单季毛利率中位数`,
    ROUND(rm.roe_ttm_median, 4) AS `TTM ROE中位数`
FROM scored s
LEFT JOIN gm_median gm
  ON gm.industry = s.industry
LEFT JOIN roe_median rm
  ON rm.industry = s.industry
GROUP BY s.industry, gm.latest_gross_margin_median, rm.roe_ttm_median
ORDER BY `占比` DESC, `满足数` DESC, `行业`;
