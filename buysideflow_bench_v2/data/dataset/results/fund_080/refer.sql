WITH anchor_date AS (
    SELECT DATE('2025-12-31') AS anchor_day
),
person_info AS (
    SELECT
        p.PERSONALCODE,
        p.EDUCATION,
        CASE
            WHEN p.EDUCATION IN (2, 3, 4, 5, 6, 7, 8) THEN p.EDUCATION
            ELSE 999
        END AS education_code,
        ROW_NUMBER() OVER (
            PARTITION BY p.PERSONALCODE
            ORDER BY p.INSERTTIME DESC, p.ID DESC
        ) AS rn
    FROM mf_personalinfo p
    JOIN anchor_date ad
      ON 1 = 1
    WHERE p.INSERTTIME < DATE_ADD(ad.anchor_day, INTERVAL 1 DAY)
),
main_funds AS (
    SELECT
        fa.INNERCODE
    FROM mf_fundarchives fa
    JOIN anchor_date ad
      ON 1 = 1
    WHERE COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= ad.anchor_day
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > ad.anchor_day)
),
incumbent_mgr_asof AS (
    SELECT DISTINCT
        fm.PERSONALCODE
    FROM mf_fundmanagernew fm
    JOIN main_funds mf
      ON mf.INNERCODE = fm.INNERCODE
    JOIN anchor_date ad
      ON 1 = 1
    WHERE fm.POSTNAME = 1
      AND fm.ACCESSIONDATE <= ad.anchor_day
      AND (fm.DIMISSIONDATE IS NULL OR fm.DIMISSIONDATE > ad.anchor_day)
      AND fm.INFOPUBLDATE < DATE_ADD(ad.anchor_day, INTERVAL 1 DAY)
)
SELECT
    CASE pi.EDUCATION
        WHEN 2 THEN '博士'
        WHEN 3 THEN '硕士'
        WHEN 4 THEN '本科'
        WHEN 5 THEN '大专'
        WHEN 6 THEN '高中'
        WHEN 7 THEN '中专'
        WHEN 8 THEN '其他'
        ELSE '未知'
    END AS `学历`,
    COUNT(DISTINCT im.PERSONALCODE) AS `基金经理人数`
FROM incumbent_mgr_asof im
LEFT JOIN person_info pi
  ON pi.PERSONALCODE = im.PERSONALCODE
 AND pi.rn = 1
GROUP BY COALESCE(pi.education_code, 999), `学历`
ORDER BY `基金经理人数` DESC, COALESCE(pi.education_code, 999) ASC;
