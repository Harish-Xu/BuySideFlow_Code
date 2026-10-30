WITH params AS (
    SELECT
        DATE('2025-10-20') AS as_of_date,
        TIMESTAMP('2025-10-20 23:59:59') AS as_of_dt,
        DATE('2025-09-30') AS report_end
),
target_dates AS (
    SELECT DATE('2024-12-31') AS end_date UNION ALL
    SELECT DATE('2025-03-31') UNION ALL
    SELECT DATE('2025-06-30') UNION ALL
    SELECT DATE('2025-09-30')
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
          ON ls.CHANGEDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) <= p.as_of_dt
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
stib_list_status_asof AS (
    SELECT INNERCODE, change_type
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE AS change_type,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY ls.CHANGEDATE DESC,
                         ls.INSERTTIME DESC,
                         ls.JSID DESC,
                         ls.ID DESC
            ) AS rn
        FROM lc_stibliststatus ls
        JOIN params p
          ON ls.CHANGEDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND ls.INSERTTIME <= p.as_of_dt
        WHERE ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
stock_base AS (
    SELECT INNERCODE, COMPANYCODE, SECUCODE, stock_name, LISTEDSECTOR
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUCODE,
            s.LISTEDSECTOR,
            COALESCE(NULLIF(s.SECUABBR, ''), NULLIF(s.CHINAMEABBR, ''), s.CHINAME, s.SECUCODE) AS stock_name,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.SECUCODE, s.INNERCODE
            ) AS rn
        FROM secumain s
        JOIN params p
          ON s.LISTEDDATE <= p.as_of_date
        LEFT JOIN list_status_asof lsa
          ON lsa.INNERCODE = s.INNERCODE
        LEFT JOIN stib_list_status_asof slsa
          ON slsa.INNERCODE = s.INNERCODE
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2, 6, 7)
          AND s.COMPANYCODE IS NOT NULL
          AND (
                (s.LISTEDSECTOR <> 7 AND lsa.change_type IN (1, 3))
             OR (s.LISTEDSECTOR = 7 AND slsa.change_type IN (1, 3))
          )
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              WHERE ra.INNERCODE = s.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND ra.IMPLEMENTDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= p.as_of_dt
                AND (
                    ra.REMOVEDATE IS NULL
                    OR ra.REMOVEDATE >= DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
                    OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.REMOVEDATE) > p.as_of_dt
                )
          )
    ) t
    WHERE rn = 1
),
regular_financial_ranked AS (
    SELECT
        m.COMPANYCODE,
        m.DEBTASSETSRATIO AS debt_asset_ratio,
        m.TOTALASSETTRATE AS asset_turnover_ratio,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY m.INFOPUBLDATE DESC,
                     COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                     m.JSID DESC,
                     m.ID DESC
        ) AS rn
    FROM lc_mainindexnew m
    JOIN stock_base sb
      ON sb.COMPANYCODE = m.COMPANYCODE
     AND sb.LISTEDSECTOR <> 7
    JOIN params p
      ON m.ENDDATE = p.report_end
     AND m.INFOPUBLDATE <= p.as_of_dt
),
stib_financial_ranked AS (
    SELECT
        m.COMPANYCODE,
        m.DEBTASSETSRATIO AS debt_asset_ratio,
        m.TOTALASSETTRATE AS asset_turnover_ratio,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY m.INFOPUBLDATE DESC,
                     COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                     m.JSID DESC,
                     m.ID DESC
        ) AS rn
    FROM lc_stibmainindex m
    JOIN stock_base sb
      ON sb.COMPANYCODE = m.COMPANYCODE
     AND sb.LISTEDSECTOR = 7
    JOIN params p
      ON m.ENDDATE = p.report_end
     AND m.INFOPUBLDATE <= p.as_of_dt
    WHERE m.IFMERGED = 1
      AND m.IFADJUSTED = 2
),
latest_financial AS (
    SELECT COMPANYCODE, debt_asset_ratio, asset_turnover_ratio
    FROM regular_financial_ranked
    WHERE rn = 1
      AND debt_asset_ratio IS NOT NULL
      AND asset_turnover_ratio IS NOT NULL
    UNION ALL
    SELECT COMPANYCODE, debt_asset_ratio, asset_turnover_ratio
    FROM stib_financial_ranked
    WHERE rn = 1
      AND debt_asset_ratio IS NOT NULL
      AND asset_turnover_ratio IS NOT NULL
),
quarter_roa_ranked AS (
    SELECT
        q.COMPANYCODE,
        q.ENDDATE AS end_date,
        q.ROA,
        ROW_NUMBER() OVER (
            PARTITION BY q.COMPANYCODE, q.ENDDATE
            ORDER BY q.INFOPUBLDATE DESC,
                     COALESCE(q.INSERTTIME, q.INFOPUBLDATE) DESC,
                     q.JSID DESC,
                     q.ID DESC
        ) AS rn
    FROM lc_qfinancialindexnew q
    JOIN stock_base sb
      ON sb.COMPANYCODE = q.COMPANYCODE
    JOIN target_dates td
      ON td.end_date = q.ENDDATE
    JOIN params p
      ON q.INFOPUBLDATE <= p.as_of_dt
    WHERE q.ACCOUNTINGSTANDARDS = 1
),
quarter_roa AS (
    SELECT COMPANYCODE, end_date, ROA
    FROM quarter_roa_ranked
    WHERE rn = 1
      AND ROA IS NOT NULL
),
roa_panel AS (
    SELECT
        COMPANYCODE,
        MAX(CASE WHEN end_date = DATE('2024-12-31') THEN ROA END) AS roa_2024q4,
        MAX(CASE WHEN end_date = DATE('2025-03-31') THEN ROA END) AS roa_2025q1,
        MAX(CASE WHEN end_date = DATE('2025-06-30') THEN ROA END) AS roa_2025q2,
        MAX(CASE WHEN end_date = DATE('2025-09-30') THEN ROA END) AS roa_2025q3
    FROM quarter_roa
    GROUP BY COMPANYCODE
    HAVING COUNT(*) = 4
),
candidate_pool AS (
    SELECT
        sb.SECUCODE,
        sb.stock_name,
        lf.debt_asset_ratio,
        lf.asset_turnover_ratio,
        rp.roa_2024q4,
        rp.roa_2025q1,
        rp.roa_2025q2,
        rp.roa_2025q3,
        rp.roa_2025q3 - rp.roa_2024q4 AS roa_improvement
    FROM stock_base sb
    JOIN latest_financial lf
      ON lf.COMPANYCODE = sb.COMPANYCODE
    JOIN roa_panel rp
      ON rp.COMPANYCODE = sb.COMPANYCODE
),
ranked_sample AS (
    SELECT
        c.*,
        PERCENT_RANK() OVER (ORDER BY debt_asset_ratio ASC) AS debt_asset_percentile,
        PERCENT_RANK() OVER (ORDER BY asset_turnover_ratio DESC) AS asset_turnover_percentile,
        PERCENT_RANK() OVER (ORDER BY roa_improvement DESC) AS roa_improvement_percentile
    FROM candidate_pool c
)
SELECT
    SECUCODE AS stock_code,
    stock_name,
    ROUND(debt_asset_ratio, 4) AS debt_asset_ratio,
    ROUND(asset_turnover_ratio, 4) AS asset_turnover_ratio,
    ROUND(roa_improvement_percentile, 4) AS roa_improvement_percentile
FROM ranked_sample
WHERE debt_asset_percentile <= 0.30
  AND asset_turnover_percentile <= 0.25
  AND roa_2024q4 < roa_2025q1
  AND roa_2025q1 < roa_2025q2
  AND roa_2025q2 < roa_2025q3
  AND roa_improvement_percentile <= 0.20
ORDER BY SECUCODE;
