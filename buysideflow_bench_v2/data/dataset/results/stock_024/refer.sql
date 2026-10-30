WITH
params AS (
    SELECT
        TIMESTAMP('2025-08-31 23:59:59') AS as_of_dt,
        DATE('2025-06-30') AS report_end,
        DATE('2024-12-31') AS prior_year_end,
        DATE('2024-06-30') AS prior_same_period
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
          ON ls.CHANGEDATE <= p.as_of_dt
         AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) <= p.as_of_dt
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
stock_base AS (
    SELECT
        INNERCODE,
        COMPANYCODE,
        SECUCODE,
        LISTEDSECTOR,
        stock_name
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUCODE,
            s.LISTEDSECTOR,
            COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name,
            ROW_NUMBER() OVER (PARTITION BY s.COMPANYCODE ORDER BY s.INNERCODE) AS rn
        FROM secumain s
        JOIN params p
          ON s.LISTEDDATE <= p.as_of_dt
        LEFT JOIN listed_asof la
          ON la.INNERCODE = s.INNERCODE
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2, 6, 7)
          AND la.CHANGETYPE IN (1, 3)
    ) t
    WHERE rn = 1
),
industry_asof AS (
    SELECT
        COMPANYCODE,
        FIRSTINDUSTRYNAME
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
          ON e.INFOPUBLDATE <= p.as_of_dt
         AND COALESCE(e.INSERTTIME, e.INFOPUBLDATE) <= p.as_of_dt
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_dt)
    ) t
    WHERE rn = 1
    UNION ALL
    SELECT
        COMPANYCODE,
        FIRSTINDUSTRYNAME
    FROM (
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
          ON e.INFOPUBLDATE <= p.as_of_dt
         AND COALESCE(e.INSERTTIME, e.INFOPUBLDATE) <= p.as_of_dt
        WHERE e.STANDARD = 38
          AND e.EFFECTIVEDATE <= p.as_of_dt
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_dt)
    ) t
    WHERE rn = 1
),
nonstib_mainindex_ver AS (
    SELECT
        m.COMPANYCODE,
        m.GROSSINCOMERATIOTTM,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn_ver
    FROM lc_mainindexnew m
    JOIN stock_base sb
      ON sb.COMPANYCODE = m.COMPANYCODE
     AND sb.LISTEDSECTOR <> 7
    JOIN params p
      ON m.INFOPUBLDATE <= p.as_of_dt
     AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD(DATE(p.as_of_dt), INTERVAL 1 DAY)
    WHERE m.ENDDATE = p.report_end
),
stib_mainindex_ver AS (
    SELECT
        COMPANYCODE,
        GROSSINCOMERATIOTTM
    FROM (
        SELECT
            m.COMPANYCODE,
            m.GROSSINCOMERATIOTTM,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE, m.ENDDATE
                ORDER BY CASE WHEN m.IFADJUSTED = 1 THEN 0 ELSE 1 END,
                         m.INFOPUBLDATE DESC,
                         m.JSID DESC
            ) AS rn_ver
        FROM lc_stibmainindex m
        JOIN stock_base sb
          ON sb.COMPANYCODE = m.COMPANYCODE
         AND sb.LISTEDSECTOR = 7
        JOIN params p
          ON m.INFOPUBLDATE <= p.as_of_dt
         AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) <= p.as_of_dt
        WHERE m.ENDDATE = p.report_end
          AND m.IFMERGED = 1
    ) t
    WHERE rn_ver = 1
),
latest_mainindex AS (
    SELECT
        COMPANYCODE,
        GROSSINCOMERATIOTTM
    FROM nonstib_mainindex_ver
    WHERE rn_ver = 1
    UNION ALL
    SELECT
        COMPANYCODE,
        GROSSINCOMERATIOTTM
    FROM stib_mainindex_ver
),
fd_ver AS (
    SELECT
        f.COMPANYCODE,
        DATE(f.ENDDATE) AS end_date,
        f.TOTALOPERATEREVENUEPS,
        f.MAININCOMEPS,
        f.RANDDEXPENSERATE,
        ROW_NUMBER() OVER (
            PARTITION BY f.COMPANYCODE, f.ENDDATE
            ORDER BY CASE f.MARK
                         WHEN 1 THEN 0
                         WHEN 8 THEN 1
                         WHEN 7 THEN 2
                         WHEN 6 THEN 3
                         WHEN 2 THEN 4
                         ELSE 9
                     END,
                     f.INFOPUBLDATE DESC, f.JSID DESC
        ) AS rn_ver
    FROM fin_derivative f
    JOIN params p
      ON f.INFOPUBLDATE <= p.as_of_dt
     AND COALESCE(f.INSERTTIME, f.INFOPUBLDATE) < DATE_ADD(DATE(p.as_of_dt), INTERVAL 1 DAY)
    WHERE DATE(f.ENDDATE) IN (p.report_end, p.prior_year_end, p.prior_same_period)
      AND f.MARK IN (1, 8, 7, 6, 2)
),
rd_periods AS (
    SELECT
        f.COMPANYCODE,
        f.end_date,
        f.TOTALOPERATEREVENUEPS,
        f.MAININCOMEPS,
        f.RANDDEXPENSERATE,
        f.TOTALOPERATEREVENUEPS * f.RANDDEXPENSERATE / 100 AS rd_expense_ps
    FROM fd_ver f
    WHERE f.rn_ver = 1
      AND f.RANDDEXPENSERATE IS NOT NULL
      AND f.TOTALOPERATEREVENUEPS IS NOT NULL
      AND f.MAININCOMEPS IS NOT NULL
),
ttm_rd AS (
    SELECT
        rp.COMPANYCODE,
        (
            MAX(CASE WHEN rp.end_date = p.prior_year_end THEN rp.rd_expense_ps END)
          + MAX(CASE WHEN rp.end_date = p.report_end THEN rp.rd_expense_ps END)
          - MAX(CASE WHEN rp.end_date = p.prior_same_period THEN rp.rd_expense_ps END)
        ) / NULLIF(
            MAX(CASE WHEN rp.end_date = p.prior_year_end THEN rp.MAININCOMEPS END)
          + MAX(CASE WHEN rp.end_date = p.report_end THEN rp.MAININCOMEPS END)
          - MAX(CASE WHEN rp.end_date = p.prior_same_period THEN rp.MAININCOMEPS END),
            0
        ) * 100 AS rd_intensity_ttm,
        COUNT(*) AS period_count
    FROM rd_periods rp
    CROSS JOIN params p
    GROUP BY rp.COMPANYCODE
),
gross_margin_universe AS (
    SELECT
        sb.COMPANYCODE,
        sb.SECUCODE,
        sb.stock_name,
        ia.FIRSTINDUSTRYNAME,
        mi.GROSSINCOMERATIOTTM
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    JOIN latest_mainindex mi
      ON mi.COMPANYCODE = sb.COMPANYCODE
    CROSS JOIN params p
    WHERE ia.FIRSTINDUSTRYNAME IN ('电力设备', '汽车')
      AND NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = sb.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= p.as_of_dt
            AND DATE(ra.IMPLEMENTDATE) <= DATE(p.as_of_dt)
            AND (
                ra.REMOVEDATE IS NULL
                OR DATE(ra.REMOVEDATE) > DATE(p.as_of_dt)
                OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) > p.as_of_dt
            )
      )
      AND mi.GROSSINCOMERATIOTTM IS NOT NULL
),
company_metrics AS (
    SELECT
        g.SECUCODE,
        g.stock_name,
        g.FIRSTINDUSTRYNAME,
        g.GROSSINCOMERATIOTTM,
        rd.rd_intensity_ttm
    FROM gross_margin_universe g
    JOIN ttm_rd rd
      ON rd.COMPANYCODE = g.COMPANYCODE
    WHERE rd.period_count = 3
      AND rd.rd_intensity_ttm IS NOT NULL
),
industry_avg AS (
    SELECT
        FIRSTINDUSTRYNAME,
        AVG(GROSSINCOMERATIOTTM) AS industry_avg_gm_ttm
    FROM gross_margin_universe
    GROUP BY FIRSTINDUSTRYNAME
)
SELECT
    cm.SECUCODE AS `代码`,
    cm.stock_name AS `公司`,
    ROUND(cm.GROSSINCOMERATIOTTM, 4) AS `TTM销售毛利率`,
    ROUND(ia.industry_avg_gm_ttm, 4) AS `行业均值毛利率`,
    ROUND(cm.rd_intensity_ttm, 4) AS `研发强度`
FROM company_metrics cm
JOIN industry_avg ia
  ON ia.FIRSTINDUSTRYNAME = cm.FIRSTINDUSTRYNAME
WHERE cm.GROSSINCOMERATIOTTM - ia.industry_avg_gm_ttm >= 15
  AND cm.rd_intensity_ttm > 5
ORDER BY (cm.GROSSINCOMERATIOTTM - ia.industry_avg_gm_ttm) DESC,
         cm.rd_intensity_ttm DESC,
         cm.SECUCODE ASC;
