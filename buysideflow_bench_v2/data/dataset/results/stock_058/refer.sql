WITH
params AS (
    SELECT
        DATE('2025-11-30') AS as_of_date,
        TIMESTAMP('2025-11-30 23:59:59') AS as_of_dt
),
list_status_asof AS (
    SELECT INNERCODE, change_type
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE AS change_type,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY ls.CHANGEDATE DESC,
                         COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
                         ls.JSID DESC,
                         ls.ID DESC
            ) AS rn
        FROM lc_liststatus ls
        JOIN params p
          ON ls.CHANGEDATE <= p.as_of_dt
         AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) <= p.as_of_dt
        WHERE ls.SECUMARKET = 83
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
stock_base AS (
    SELECT INNERCODE, COMPANYCODE, SECUCODE, company
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUCODE,
            COALESCE(NULLIF(s.SECUABBR, ''), NULLIF(s.CHINAMEABBR, ''), s.CHINAME, s.SECUCODE) AS company,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.SECUCODE, s.INNERCODE
            ) AS rn
        FROM secumain s
        JOIN params p
          ON s.LISTEDDATE <= p.as_of_date
        JOIN list_status_asof lsa
          ON lsa.INNERCODE = s.INNERCODE
         AND lsa.change_type IN (1, 3)
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET = 83
          AND s.LISTEDSECTOR = 1
          AND s.COMPANYCODE IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              WHERE ra.INNERCODE = s.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND DATE(ra.IMPLEMENTDATE) <= p.as_of_date
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= p.as_of_dt
                AND COALESCE(ra.INSERTTIME, ra.IMPLANNOUCEDATE, ra.IMPLEMENTDATE) <= p.as_of_dt
                AND (
                    ra.REMOVEDATE IS NULL
                    OR DATE(ra.REMOVEDATE) > p.as_of_date
                    OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.REMOVEDATE) > p.as_of_dt
                )
          )
    ) t
    WHERE rn = 1
),
industry_asof AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME AS industry
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC,
                         e.CANCELDATE DESC,
                         e.JSID DESC,
                         e.ID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_dt
        WHERE e.STANDARD = 38
          AND e.FIRSTINDUSTRYNAME IS NOT NULL
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_dt)
    ) t
    WHERE rn = 1
),
candidate AS (
    SELECT
        sb.COMPANYCODE,
        sb.SECUCODE AS code,
        sb.company,
        ia.industry
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    WHERE ia.industry NOT IN ('银行', '非银金融')
),
financial_ranked AS (
    SELECT
        m.COMPANYCODE,
        YEAR(m.ENDDATE) AS report_year,
        m.NPFROMPARENTCOMPANYOWNERS AS parent_net_profit,
        m.NETPROFITCUT AS cut_net_profit,
        m.OPERATINGREENUE AS operating_revenue,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
            ORDER BY CASE m.MARK
                         WHEN 1 THEN 1
                         WHEN 8 THEN 2
                         WHEN 7 THEN 3
                         WHEN 6 THEN 4
                         WHEN 2 THEN 5
                         ELSE 9
                     END,
                     m.INFOPUBLDATE DESC,
                     COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                     m.JSID DESC,
                     m.ID DESC
        ) AS rn
    FROM lc_maindatanew m
    JOIN candidate c
      ON c.COMPANYCODE = m.COMPANYCODE
    JOIN params p
     ON m.INFOPUBLDATE <= p.as_of_dt
     AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) <= p.as_of_dt
    WHERE m.MARK IN (1, 8, 7, 6, 2)
      AND m.INFOSOURCECODE IN (110101, 120102)  -- 年报、更正后年报；不用招股书或季报中的比较数
      AND m.ACCOUNTINGSTANDARDS = 1
      AND DATE(m.ENDDATE) IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
      AND m.NPFROMPARENTCOMPANYOWNERS IS NOT NULL
      AND m.NETPROFITCUT IS NOT NULL
      AND m.OPERATINGREENUE IS NOT NULL
),
annual_financial AS (
    SELECT
        COMPANYCODE,
        report_year,
        parent_net_profit,
        cut_net_profit,
        operating_revenue
    FROM financial_ranked
    WHERE rn = 1
),
company_metrics AS (
    SELECT
        COMPANYCODE,
        COUNT(*) AS year_count,
        MIN(parent_net_profit) AS min_parent_net_profit,
        MIN(operating_revenue) AS min_operating_revenue,
        MAX(CASE WHEN report_year = 2022 THEN cut_net_profit / NULLIF(parent_net_profit, 0) END) AS cut_ratio_2022,
        MAX(CASE WHEN report_year = 2023 THEN cut_net_profit / NULLIF(parent_net_profit, 0) END) AS cut_ratio_2023,
        MAX(CASE WHEN report_year = 2024 THEN cut_net_profit / NULLIF(parent_net_profit, 0) END) AS cut_ratio_2024,
        MAX(CASE WHEN report_year = 2022 THEN cut_net_profit / NULLIF(operating_revenue, 0) END) AS cut_margin_2022,
        MAX(CASE WHEN report_year = 2023 THEN cut_net_profit / NULLIF(operating_revenue, 0) END) AS cut_margin_2023,
        MAX(CASE WHEN report_year = 2024 THEN cut_net_profit / NULLIF(operating_revenue, 0) END) AS cut_margin_2024
    FROM annual_financial
    GROUP BY COMPANYCODE
),
comparable_pool AS (
    SELECT
        c.COMPANYCODE,
        c.industry,
        c.code,
        c.company,
        m.cut_ratio_2022,
        m.cut_ratio_2023,
        m.cut_ratio_2024,
        m.cut_margin_2022,
        m.cut_margin_2023,
        m.cut_margin_2024
    FROM candidate c
    JOIN company_metrics m
      ON m.COMPANYCODE = c.COMPANYCODE
    WHERE m.year_count = 3
      AND m.min_parent_net_profit > 0
      AND m.min_operating_revenue > 0
),
core_profit_hit AS (
    SELECT *
    FROM comparable_pool
    WHERE cut_ratio_2022 > 0.8
      AND cut_ratio_2023 > 0.8
      AND cut_ratio_2024 > 0.8
      AND cut_margin_2022 < cut_margin_2023
      AND cut_margin_2023 < cut_margin_2024
),
industry_total AS (
    SELECT industry, COUNT(*) AS comparable_count
    FROM comparable_pool
    GROUP BY industry
),
industry_hit AS (
    SELECT industry, COUNT(*) AS hit_count
    FROM core_profit_hit
    GROUP BY industry
),
top_industries AS (
    SELECT
        industry,
        hit_count,
        comparable_count,
        penetration,
        ROW_NUMBER() OVER (
            ORDER BY penetration DESC, hit_count DESC, industry ASC
        ) AS industry_rank
    FROM (
        SELECT
            h.industry,
            h.hit_count,
            t.comparable_count,
            h.hit_count / NULLIF(t.comparable_count, 0) AS penetration
        FROM industry_hit h
        JOIN industry_total t
          ON t.industry = h.industry
    ) s
),
ranked_detail AS (
    SELECT
        t.industry_rank,
        t.industry,
        t.hit_count,
        t.comparable_count,
        t.penetration,
        h.code,
        h.company,
        LEAST(h.cut_ratio_2022, h.cut_ratio_2023, h.cut_ratio_2024) AS minimum_cut_ratio,
        h.cut_margin_2024 - h.cut_margin_2022 AS cut_margin_change,
        h.cut_margin_2024,
        ROW_NUMBER() OVER (
            PARTITION BY t.industry
            ORDER BY h.cut_margin_2024 DESC, h.code ASC
        ) AS company_rank
    FROM top_industries t
    JOIN core_profit_hit h
      ON h.industry = t.industry
    WHERE t.industry_rank <= 5
)
SELECT
    industry AS `行业`,
    hit_count AS `核心利润强化样本数`,
    comparable_count AS `行业可比样本数`,
    ROUND(penetration * 100, 2) AS `渗透率`,
    code AS `代码`,
    company AS `公司`,
    ROUND(minimum_cut_ratio * 100, 2) AS `三年最低扣非净利占比`,
    ROUND(cut_margin_change * 100, 2) AS `扣非净利率变化`,
    ROUND(cut_margin_2024 * 100, 2) AS `2024年扣非净利率`
FROM ranked_detail
WHERE company_rank <= 3
ORDER BY industry_rank, company_rank;
