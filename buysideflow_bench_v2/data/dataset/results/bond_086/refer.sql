WITH params AS (
    SELECT
        DATE('2025-01-01') AS start_date,
        DATE('2025-12-31') AS as_of_date,
        TIMESTAMP('2026-01-01') AS next_ts
),
raw_issue AS (
    SELECT
        CAST(i.MAINCODE AS UNSIGNED) AS MAINCODE,
        DATE(COALESCE(i.ISSUEDATESTART, i.BIDSTARTDATE, i.PUBLICANNOUNCEMENTDATE)) AS issue_date,
        i.PLANISSUESIZE,
        i.ACTUALISSUESIZE,
        i.PARVALUE AS issue_parvalue,
        i.ISSUEPRICE AS issue_price,
        i.REMARK AS issue_remark
    FROM bond_issuenew i
    CROSS JOIN params p
    WHERE COALESCE(i.ISSUEDATESTART, i.BIDSTARTDATE, i.PUBLICANNOUNCEMENTDATE) >= p.start_date
      AND COALESCE(i.ISSUEDATESTART, i.BIDSTARTDATE, i.PUBLICANNOUNCEMENTDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND i.PLANISSUESIZE IS NOT NULL
      AND i.PLANISSUESIZE > 0
      AND i.ACTUALISSUESIZE IS NOT NULL
      AND i.ACTUALISSUESIZE > 0
),
issue_single AS (
    SELECT
        MAINCODE,
        MIN(issue_date) AS issue_date,
        MIN(PLANISSUESIZE) AS plan_issue_size_mn,
        MIN(ACTUALISSUESIZE) AS actual_issue_size_mn,
        MIN(issue_parvalue) AS issue_parvalue,
        MIN(issue_price) AS issue_price,
        GROUP_CONCAT(DISTINCT COALESCE(issue_remark, '') SEPARATOR ' ') AS issue_text
    FROM raw_issue
    GROUP BY MAINCODE
    HAVING COUNT(DISTINCT ROUND(PLANISSUESIZE, 8)) = 1
       AND COUNT(DISTINCT ROUND(ACTUALISSUESIZE, 8)) = 1
),
code_agg AS (
    SELECT
        CAST(bc.MAINCODE AS UNSIGNED) AS MAINCODE,
        SUBSTRING_INDEX(
            GROUP_CONCAT(
                bc.SECUCODE
                ORDER BY CASE WHEN bc.SECUMARKET = 89 THEN 0 ELSE 1 END,
                         bc.SECUMARKET,
                         bc.SECUCODE
                SEPARATOR ','
            ),
            ',',
            1
        ) AS bond_code,
        CAST(
            SUBSTRING_INDEX(
                GROUP_CONCAT(
                    CAST(bc.INNERCODE AS UNSIGNED)
                    ORDER BY CASE WHEN bc.SECUMARKET = 89 THEN 0 ELSE 1 END,
                             bc.SECUMARKET,
                             bc.SECUCODE
                    SEPARATOR ','
                ),
                ',',
                1
            ) AS UNSIGNED
        ) AS innercode,
        CAST(
            SUBSTRING_INDEX(
                GROUP_CONCAT(
                    CAST(bc.SECUMARKET AS UNSIGNED)
                    ORDER BY CASE WHEN bc.SECUMARKET = 89 THEN 0 ELSE 1 END,
                             bc.SECUMARKET,
                             bc.SECUCODE
                    SEPARATOR ','
                ),
                ',',
                1
            ) AS UNSIGNED
        ) AS listed_market,
        SUBSTRING_INDEX(
            GROUP_CONCAT(
                bc.SECUABBR
                ORDER BY CASE WHEN bc.SECUMARKET = 89 THEN 0 ELSE 1 END,
                         bc.SECUMARKET,
                         bc.SECUCODE
                SEPARATOR ','
            ),
            ',',
            1
        ) AS bond_name,
        MIN(DATE(bc.LISTEDDATE)) AS code_listed_date,
        MAX(
            CASE
                WHEN bc.BONDTYPELEVEL1 IN (1600,1700,1800,2500,2600,2800,2900,3100,3200,3300,3700)
                  OR bc.BONDTYPELEVEL2 IN (1901,1903)
                THEN 1 ELSE 0
            END
        ) AS is_enterprise_credit,
        GROUP_CONCAT(DISTINCT COALESCE(bc.SECUABBR, '') SEPARATOR ' ') AS code_text
    FROM bond_code bc
    CROSS JOIN params p
    WHERE bc.MAINCODE IS NOT NULL
      AND bc.INNERCODE IS NOT NULL
    GROUP BY bc.MAINCODE
),
basic_agg AS (
    SELECT
        CAST(bi.MAINCODE AS UNSIGNED) AS MAINCODE,
        MIN(DATE(bi.LISTEDDATE)) AS basic_listed_date,
        MIN(bi.PARVALUE) AS basic_parvalue,
        MIN(bi.ISSUEPRICE) AS basic_issue_price,
        MAX(CASE WHEN bi.RATEIFFLOAT = 2 THEN 1 ELSE 0 END) AS has_fixed_flag,
        MAX(CASE WHEN bi.RATEIFFLOAT = 1 THEN 1 ELSE 0 END) AS has_float_flag,
        MAX(CASE WHEN bi.COMPOUNDMETHOD = 1 THEN 1 ELSE 0 END) AS has_simple_fixed,
        MAX(CASE WHEN bi.COUPONRATE IS NOT NULL AND bi.COUPONRATE > 0 THEN 1 ELSE 0 END) AS has_coupon,
        GROUP_CONCAT(
            DISTINCT CONCAT_WS(' ', bi.BONDFULLNAME, bi.OPTIONTYPE, bi.OPMATURITY, bi.REMARK)
            SEPARATOR ' '
        ) AS basic_text
    FROM bond_basicinfo bi
    CROSS JOIN params p
    WHERE bi.MAINCODE IS NOT NULL
    GROUP BY bi.MAINCODE
),
candidate AS (
    SELECT
        i.MAINCODE,
        ca.innercode,
        ca.listed_market,
        ca.bond_code,
        ca.bond_name,
        i.issue_date,
        DATE(COALESCE(ca.code_listed_date, ba.basic_listed_date)) AS listed_date,
        i.plan_issue_size_mn,
        i.actual_issue_size_mn,
        i.actual_issue_size_mn / NULLIF(i.plan_issue_size_mn, 0) AS issue_size_ratio,
        COALESCE(i.issue_parvalue, ba.basic_parvalue) AS parvalue,
        COALESCE(i.issue_price, ba.basic_issue_price) AS issue_price,
        CONCAT_WS(' ', ca.code_text, ba.basic_text, i.issue_text) AS all_text
    FROM issue_single i
    JOIN code_agg ca
      ON ca.MAINCODE = i.MAINCODE
    JOIN basic_agg ba
      ON ba.MAINCODE = i.MAINCODE
    CROSS JOIN params p
    WHERE ca.is_enterprise_credit = 1
      AND ca.bond_code IS NOT NULL
      AND ca.bond_name IS NOT NULL
      AND COALESCE(ba.has_fixed_flag, 0) = 1
      AND COALESCE(ba.has_float_flag, 0) = 0
      AND COALESCE(ba.has_simple_fixed, 0) = 1
      AND COALESCE(ba.has_coupon, 0) = 1
      AND ABS(COALESCE(i.issue_parvalue, ba.basic_parvalue) - 100) < 0.000001
      AND ABS(COALESCE(i.issue_price, ba.basic_issue_price) - 100) < 0.000001
      AND COALESCE(ca.code_listed_date, ba.basic_listed_date) IS NOT NULL
      AND DATE(COALESCE(ca.code_listed_date, ba.basic_listed_date)) <= p.as_of_date
)
SELECT
    MAINCODE,
    innercode,
    listed_market,
    bond_code,
    bond_name,
    issue_date,
    listed_date,
    plan_issue_size_mn,
    actual_issue_size_mn,
    issue_size_ratio,
    CASE
        WHEN issue_size_ratio >= 1.1 THEN '放量组'
        WHEN issue_size_ratio <= 0.9 THEN '缩量组'
        ELSE '中性'
    END AS issue_group
FROM candidate
WHERE all_text NOT REGEXP '永续|续期|可续期|次级|二级资本'
ORDER BY issue_size_ratio DESC, bond_code;
