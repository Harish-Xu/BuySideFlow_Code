WITH
params AS (
    SELECT DATE('2025-04-30') AS as_of_date
),
target_indices AS (
    SELECT
        INNERCODE,
        SECUCODE,
        COALESCE(SECUABBR, CHINAMEABBR, CHINAME, SECUCODE) AS index_name
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE IN ('000300', '000905')
),
index_constituents AS (
    SELECT DISTINCT
        ti.SECUCODE AS index_code,
        ti.index_name,
        sm.COMPANYCODE,
        sm.INNERCODE,
        sm.LISTEDSECTOR
    FROM lc_indexcomponent ic
    JOIN target_indices ti
      ON ti.INNERCODE = ic.INDEXINNERCODE
    JOIN params p
      ON ic.INDATE <= p.as_of_date
    JOIN secumain sm
      ON sm.INNERCODE = ic.SECUINNERCODE
    WHERE (ic.OUTDATE IS NULL OR ic.OUTDATE > p.as_of_date)
      AND sm.SECUCATEGORY = 1
),
stock_info AS (
    SELECT
        index_code,
        index_name,
        COMPANYCODE,
        SECUCODE,
        company,
        LISTEDSECTOR
    FROM (
        SELECT
            ic.index_code,
            ic.index_name,
            ic.COMPANYCODE,
            ic.LISTEDSECTOR,
            s.SECUCODE,
            COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS company,
            ROW_NUMBER() OVER (
                PARTITION BY ic.index_code, ic.COMPANYCODE
                ORDER BY s.INNERCODE
            ) AS rn
        FROM index_constituents ic
        JOIN secumain s
          ON s.INNERCODE = ic.INNERCODE
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
        JOIN stock_info si
          ON si.COMPANYCODE = e.COMPANYCODE
         AND si.LISTEDSECTOR <> 7
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_date
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
        UNION ALL
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
            ) AS rn
        FROM lc_stibexgindustry e
        JOIN stock_info si
          ON si.COMPANYCODE = e.COMPANYCODE
         AND si.LISTEDSECTOR = 7
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_date
        WHERE e.STANDARD = 38
          AND e.EFFECTIVEDATE <= p.as_of_date
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    ) t
    WHERE rn = 1
),
quarter_pairs AS (
    SELECT DATE('2024-03-31') AS cur_end, DATE('2023-03-31') AS prev_end
    UNION ALL SELECT DATE('2024-06-30'), DATE('2023-06-30')
    UNION ALL SELECT DATE('2024-09-30'), DATE('2023-09-30')
    UNION ALL SELECT DATE('2024-12-31'), DATE('2023-12-31')
),
quarter_fin AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        GROSSINCOMERATIO,
        NETPROFITRATIO
    FROM (
        SELECT
            q.COMPANYCODE,
            q.ENDDATE,
            q.GROSSINCOMERATIO,
            q.NETPROFITRATIO,
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
        JOIN stock_info si
          ON si.COMPANYCODE = q.COMPANYCODE
        JOIN params p
          ON q.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE q.MARK IN (1, 8, 7, 6, 2)
          AND q.ACCOUNTINGSTANDARDS = 1
          AND q.ENDDATE IN (
              DATE('2023-03-31'), DATE('2023-06-30'), DATE('2023-09-30'), DATE('2023-12-31'),
              DATE('2024-03-31'), DATE('2024-06-30'), DATE('2024-09-30'), DATE('2024-12-31')
          )
          AND q.GROSSINCOMERATIO IS NOT NULL
          AND q.NETPROFITRATIO IS NOT NULL
    ) t
    WHERE rn = 1
),
paired AS (
    SELECT
        si.index_name,
        ia.FIRSTINDUSTRYNAME AS industry,
        si.SECUCODE,
        si.company,
        cur.GROSSINCOMERATIO - prev.GROSSINCOMERATIO AS gm_diff,
        cur.NETPROFITRATIO - prev.NETPROFITRATIO AS npm_diff
    FROM stock_info si
    JOIN industry_asof ia
      ON ia.COMPANYCODE = si.COMPANYCODE
    JOIN quarter_pairs qp
    JOIN quarter_fin cur
      ON cur.COMPANYCODE = si.COMPANYCODE
     AND cur.ENDDATE = qp.cur_end
    JOIN quarter_fin prev
      ON prev.COMPANYCODE = si.COMPANYCODE
     AND prev.ENDDATE = qp.prev_end
),
scored AS (
    SELECT
        index_name,
        industry,
        SECUCODE,
        company,
        SUM(gm_diff) AS gross_margin_cum_improve,
        SUM(npm_diff) AS net_margin_cum_improve,
        COUNT(*) AS pair_cnt
    FROM paired
    GROUP BY index_name, industry, SECUCODE, company
    HAVING COUNT(*) = 4
),
qualified AS (
    SELECT *
    FROM scored
    WHERE gross_margin_cum_improve > 5
),
industry_stats AS (
    SELECT
        index_name,
        industry,
        COUNT(*) AS hit_count
    FROM qualified
    GROUP BY index_name, industry
),
ranked AS (
    SELECT
        q.*,
        ROW_NUMBER() OVER (
            PARTITION BY q.index_name, q.industry
            ORDER BY q.gross_margin_cum_improve DESC, q.SECUCODE
        ) AS rn
    FROM qualified q
)
SELECT
    s.index_name AS `指数`,
    s.industry AS `行业`,
    s.hit_count AS `满足数`,
    r.SECUCODE AS `代码`,
    r.company AS `公司`,
    ROUND(r.gross_margin_cum_improve, 4) AS `毛利率累计改善`,
    ROUND(r.net_margin_cum_improve, 4) AS `近四季单季净利率累计变化`
FROM industry_stats s
LEFT JOIN ranked r
  ON r.index_name = s.index_name
 AND r.industry = s.industry
 AND r.rn = 1
ORDER BY `指数`, `满足数` DESC, `行业`, `代码`;
