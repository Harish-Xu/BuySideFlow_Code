WITH
params AS (
    SELECT
        DATE('2025-04-30') AS as_of_date,
        TIMESTAMP('2025-04-30 23:59:59') AS as_of_dt
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
        COMPANYCODE,
        INNERCODE,
        SECUCODE,
        company
    FROM (
        SELECT
            s.COMPANYCODE,
            s.INNERCODE,
            s.SECUCODE,
            COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS company,
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
    ) s
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
                         e.CANCELDATE DESC,
                         e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_dt
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_dt)
          AND e.FIRSTINDUSTRYNAME IN ('家用电器', '汽车', '纺织服饰', '轻工制造', '商贸零售', '社会服务')
    ) t
    WHERE rn = 1
),
candidate AS (
    SELECT
        sb.COMPANYCODE,
        sb.SECUCODE,
        sb.company,
        ia.FIRSTINDUSTRYNAME AS industry
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
),
financial_ranked AS (
    SELECT
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS end_date,
        YEAR(m.ENDDATE) AS report_year,
        CASE WHEN MONTH(m.ENDDATE) = 9 THEN 'q3' ELSE 'annual' END AS period_type,
        m.NPFROMPARENTCOMPANYOWNERS AS np_parent,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
                ORDER BY CASE
                         WHEN m.MARK = 1 THEN 1
                         WHEN m.MARK = 8 THEN 2
                         WHEN m.MARK = 7 THEN 3
                         WHEN m.MARK = 6 THEN 4
                         WHEN m.MARK = 2 THEN 5
                         ELSE 9
                     END,
                     m.INFOPUBLDATE DESC,
                     COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                     m.JSID DESC
        ) AS rn
    FROM lc_maindatanew m
    JOIN candidate c
      ON c.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.INFOPUBLDATE <= p.as_of_dt
    WHERE COALESCE(m.ACCOUNTINGSTANDARDS, 1) = 1
      AND m.MARK IN (1, 8, 7, 6, 2)
      AND DATE(m.ENDDATE) IN (
          DATE('2022-09-30'), DATE('2022-12-31'),
          DATE('2023-09-30'), DATE('2023-12-31'),
          DATE('2024-09-30'), DATE('2024-12-31')
      )
      AND m.NPFROMPARENTCOMPANYOWNERS IS NOT NULL
),
financial_points AS (
    SELECT
        COMPANYCODE,
        report_year,
        period_type,
        np_parent
    FROM financial_ranked
    WHERE rn = 1
),
year_panel AS (
    SELECT
        c.industry,
        c.SECUCODE,
        c.company,
        a.COMPANYCODE,
        a.report_year,
        a.np_parent AS annual_np,
        q.np_parent AS q3_np,
        a.np_parent - q.np_parent AS q4_np,
        (a.np_parent - q.np_parent) / a.np_parent * 100 AS q4_ratio
    FROM candidate c
    JOIN financial_points a
      ON a.COMPANYCODE = c.COMPANYCODE
     AND a.period_type = 'annual'
    JOIN financial_points q
      ON q.COMPANYCODE = a.COMPANYCODE
     AND q.report_year = a.report_year
     AND q.period_type = 'q3'
    WHERE a.np_parent > 0
),
ratios AS (
    SELECT
        industry,
        SECUCODE,
        company,
        MAX(CASE WHEN report_year = 2022 THEN q4_ratio END) AS q4_ratio_2022,
        MAX(CASE WHEN report_year = 2023 THEN q4_ratio END) AS q4_ratio_2023,
        MAX(CASE WHEN report_year = 2024 THEN q4_ratio END) AS q4_ratio_2024
    FROM year_panel
    GROUP BY industry, SECUCODE, company
    HAVING COUNT(DISTINCT report_year) = 3
),
flagged AS (
    SELECT
        r.*,
        CASE
            WHEN r.q4_ratio_2022 > 50 OR r.q4_ratio_2022 < 10
              OR r.q4_ratio_2023 > 50 OR r.q4_ratio_2023 < 10
              OR r.q4_ratio_2024 > 50 OR r.q4_ratio_2024 < 10
            THEN 1 ELSE 0
        END AS is_abnormal,
        GREATEST(
            CASE WHEN r.q4_ratio_2022 > 50 THEN r.q4_ratio_2022 - 50 WHEN r.q4_ratio_2022 < 10 THEN 10 - r.q4_ratio_2022 ELSE 0 END,
            CASE WHEN r.q4_ratio_2023 > 50 THEN r.q4_ratio_2023 - 50 WHEN r.q4_ratio_2023 < 10 THEN 10 - r.q4_ratio_2023 ELSE 0 END,
            CASE WHEN r.q4_ratio_2024 > 50 THEN r.q4_ratio_2024 - 50 WHEN r.q4_ratio_2024 < 10 THEN 10 - r.q4_ratio_2024 ELSE 0 END
        ) AS max_deviation
    FROM ratios r
),
industry_stats AS (
    SELECT
        industry,
        COUNT(*) AS comparable_sample_count,
        CAST(SUM(is_abnormal) AS UNSIGNED) AS abnormal_count,
        ROUND(SUM(is_abnormal) / NULLIF(COUNT(*), 0) * 100, 2) AS abnormal_ratio_pct
    FROM flagged
    GROUP BY industry
),
top_industries AS (
    SELECT
        industry,
        abnormal_count,
        comparable_sample_count,
        abnormal_ratio_pct
    FROM (
        SELECT
            s.*,
            ROW_NUMBER() OVER (
                ORDER BY s.abnormal_ratio_pct DESC, s.abnormal_count DESC, s.industry
            ) AS rn
        FROM industry_stats s
        WHERE s.abnormal_count > 0
    ) t
    WHERE rn <= 5
),
ranked_detail AS (
    SELECT
        f.*,
        ROW_NUMBER() OVER (
            PARTITION BY f.industry
            ORDER BY f.max_deviation DESC, f.SECUCODE
        ) AS detail_rank
    FROM flagged f
    JOIN top_industries ti
      ON ti.industry = f.industry
    WHERE f.is_abnormal = 1
)
SELECT
    ti.industry AS `行业`,
    ti.abnormal_count AS `异常样本数`,
    ti.comparable_sample_count AS `行业可比样本数`,
    ROUND(ti.abnormal_count / NULLIF(ti.comparable_sample_count, 0) * 100, 4) AS `渗透率(%)`,
    LPAD(rd.SECUCODE, 6, '0') AS `代码`,
    rd.company AS `公司`,
    CONCAT(
        '2022:', ROUND(rd.q4_ratio_2022, 2), '% | ',
        '2023:', ROUND(rd.q4_ratio_2023, 2), '% | ',
        '2024:', ROUND(rd.q4_ratio_2024, 2), '%'
    ) AS `近三年Q4占比`
FROM top_industries ti
JOIN ranked_detail rd
  ON rd.industry = ti.industry
 AND rd.detail_rank <= 3
ORDER BY `渗透率(%)` DESC, `异常样本数` DESC, `行业`, rd.max_deviation DESC, `代码`;
