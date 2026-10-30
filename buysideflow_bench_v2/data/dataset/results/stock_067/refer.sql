WITH
params AS (
    SELECT
        DATE('2025-04-30') AS as_of_date
),
target_indices AS (
    SELECT
        s.INNERCODE,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS index_name
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE = '000905'
),
constituent_keys AS (
    SELECT DISTINCT
        ti.INNERCODE AS index_innercode,
        ti.index_name,
        ic.SECUINNERCODE AS innercode
    FROM lc_indexcomponent ic
    JOIN target_indices ti
      ON ti.INNERCODE = ic.INDEXINNERCODE
    JOIN params p
      ON ic.INDATE <= p.as_of_date
    WHERE (ic.OUTDATE IS NULL OR ic.OUTDATE > p.as_of_date)
),
index_constituents AS (
    SELECT ck.*, sm.COMPANYCODE, (sm.LISTEDSECTOR = 7) AS is_stib
    FROM constituent_keys ck
    LEFT JOIN secumain sm ON sm.INNERCODE = ck.innercode
),
issuer_pool AS (
    SELECT DISTINCT COMPANYCODE, is_stib
    FROM index_constituents
    WHERE COMPANYCODE IS NOT NULL
),
visible_report_periods AS (
    SELECT m.COMPANYCODE, m.ENDDATE, 0 AS is_stib
    FROM lc_maindatanew m
    JOIN issuer_pool s ON s.COMPANYCODE = m.COMPANYCODE AND s.is_stib = 0
    JOIN params p ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND m.ENDDATE <= p.as_of_date
    WHERE CAST(m.BULLETINTYPE AS UNSIGNED) = 20
      AND m.INFOSOURCECODE IN (110101, 110102, 110103, 110104)
    UNION ALL
    SELECT m.COMPANYCODE, m.ENDDATE, 1 AS is_stib
    FROM lc_stibmaindata m
    JOIN issuer_pool s ON s.COMPANYCODE = m.COMPANYCODE AND s.is_stib = 1
    JOIN params p ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND m.ENDDATE <= p.as_of_date
    WHERE m.INFOSOURCECODE IN (110101, 110102, 110103, 110104)
    UNION ALL
    SELECT m.COMPANYCODE, m.ENDDATE, 0 AS is_stib
    FROM lc_mainindexnew m
    JOIN issuer_pool s ON s.COMPANYCODE = m.COMPANYCODE AND s.is_stib = 0
    JOIN params p ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND m.ENDDATE <= p.as_of_date
    UNION ALL
    SELECT m.COMPANYCODE, m.ENDDATE, 1 AS is_stib
    FROM lc_stibmainindex m
    JOIN issuer_pool s ON s.COMPANYCODE = m.COMPANYCODE AND s.is_stib = 1
    JOIN params p ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND m.ENDDATE <= p.as_of_date
    WHERE m.IFMERGED = 1
),
latest_report_period AS (
    SELECT COMPANYCODE, is_stib, MAX(ENDDATE) AS report_period
    FROM visible_report_periods
    GROUP BY COMPANYCODE, is_stib
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
                ORDER BY m.ENDDATE DESC, m.INFOPUBLDATE DESC,
                         COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                         m.JSID DESC
            ) AS rn
        FROM lc_mainindexnew m
        JOIN latest_report_period rp ON rp.COMPANYCODE = m.COMPANYCODE
          AND rp.is_stib = 0 AND rp.report_period = m.ENDDATE
        JOIN issuer_pool ic
          ON ic.COMPANYCODE = m.COMPANYCODE
         AND ic.is_stib = 0
        JOIN params p
          ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE m.ENDDATE <= p.as_of_date
    ) t
    WHERE rn = 1
      AND ROETTM IS NOT NULL
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
        JOIN latest_report_period rp ON rp.COMPANYCODE = m.COMPANYCODE
          AND rp.is_stib = 1 AND rp.report_period = m.ENDDATE
        JOIN issuer_pool ic
          ON ic.COMPANYCODE = m.COMPANYCODE
         AND ic.is_stib = 1
        JOIN params p
          ON m.ENDDATE <= p.as_of_date
         AND m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE m.IFMERGED = 1
    ) t
    WHERE rn = 1
      AND ROETTM IS NOT NULL
),
latest_roe AS (
    SELECT COMPANYCODE, ROETTM, 0 AS is_stib FROM nonstib_latest_roe
    UNION ALL
    SELECT COMPANYCODE, ROETTM, 1 AS is_stib FROM stib_latest_roe
),
nonstib_annual_dividend AS (
    SELECT
        t.COMPANYCODE,
        AVG(t.DIVIDENDPAIDRATIO) AS avg_dividend_payout_ratio_3y,
        COUNT(*) AS year_cnt,
        SUM(CASE WHEN t.DIVIDENDPAIDRATIO BETWEEN 30 AND 60 THEN 1 ELSE 0 END) AS ok_year_cnt
    FROM (
        SELECT
            m.COMPANYCODE,
            m.ENDDATE,
            m.DIVIDENDPAIDRATIO,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE, m.ENDDATE
                ORDER BY m.INFOPUBLDATE DESC,
                         COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                         m.JSID DESC
            ) AS rn
        FROM lc_mainindexnew m
        JOIN issuer_pool ic
          ON ic.COMPANYCODE = m.COMPANYCODE
         AND ic.is_stib = 0
        JOIN params p
          ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE m.ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
    ) t
    WHERE rn = 1
    GROUP BY COMPANYCODE
),
stib_annual_dividend AS (
    SELECT
        t.COMPANYCODE,
        AVG(t.DIVIDENDPAIDRATIO) AS avg_dividend_payout_ratio_3y,
        COUNT(*) AS year_cnt,
        SUM(CASE WHEN t.DIVIDENDPAIDRATIO BETWEEN 30 AND 60 THEN 1 ELSE 0 END) AS ok_year_cnt
    FROM (
        SELECT
            m.COMPANYCODE,
            m.ENDDATE,
            m.DIVIDENDPAIDRATIO,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE, m.ENDDATE
                ORDER BY CASE WHEN m.IFADJUSTED = 1 THEN 0 ELSE 1 END,
                         m.INFOPUBLDATE DESC,
                         COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                         m.JSID DESC
            ) AS rn
        FROM lc_stibmainindex m
        JOIN issuer_pool ic
          ON ic.COMPANYCODE = m.COMPANYCODE
         AND ic.is_stib = 1
        JOIN params p
          ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE m.ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
          AND m.IFMERGED = 1
    ) t
    WHERE rn = 1
    GROUP BY COMPANYCODE
),
annual_dividend AS (
    SELECT t.*, 0 AS is_stib FROM nonstib_annual_dividend t
    UNION ALL
    SELECT t.*, 1 AS is_stib FROM stib_annual_dividend t
),
scored AS (
    SELECT
        ic.index_innercode,
        ic.index_name,
        ic.innercode,
        ic.COMPANYCODE,
        lr.ROETTM,
        ad.avg_dividend_payout_ratio_3y,
        ad.year_cnt,
        ad.ok_year_cnt,
        CASE
            WHEN lr.ROETTM > 12
             AND ad.year_cnt = 3
             AND ad.ok_year_cnt = 3
            THEN 1 ELSE 0
        END AS hit_flag
    FROM index_constituents ic
    LEFT JOIN latest_roe lr
      ON lr.COMPANYCODE = ic.COMPANYCODE
     AND lr.is_stib = ic.is_stib
    LEFT JOIN annual_dividend ad
      ON ad.COMPANYCODE = ic.COMPANYCODE
     AND ad.is_stib = ic.is_stib
)
SELECT
    CAST(SUM(hit_flag) AS UNSIGNED) AS `满足数`,
    COUNT(*) AS `成分证券总数`,
    ROUND(SUM(hit_flag) / NULLIF(COUNT(*), 0) * 100, 2) AS `占比`,
    ROUND(AVG(CASE WHEN hit_flag = 1 THEN ROETTM END), 4) AS `TTM ROE均值`,
    ROUND(AVG(CASE WHEN hit_flag = 1 THEN avg_dividend_payout_ratio_3y END), 4) AS `近三年年报分红率均值`
FROM scored
GROUP BY index_innercode, index_name
ORDER BY index_name;
