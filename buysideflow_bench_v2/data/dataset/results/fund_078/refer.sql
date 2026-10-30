WITH anchor_date AS (
    SELECT DATE('2025-12-31') AS anchor_day
),
person_info AS (
    SELECT
        p.PERSONALCODE,
        p.CHINESENAME,
        p.PRACTICEDATE,
        ROW_NUMBER() OVER (
            PARTITION BY p.PERSONALCODE
            ORDER BY p.INSERTTIME DESC, p.ID DESC
        ) AS rn
    FROM mf_personalinfo p
    JOIN anchor_date ad
      ON 1 = 1
    WHERE p.INSERTTIME < DATE_ADD(ad.anchor_day, INTERVAL 1 DAY)
),
fof_main_funds AS (
    SELECT
        fa.INNERCODE
    FROM mf_fundarchives fa
    JOIN anchor_date ad
      ON 1 = 1
    WHERE COALESCE(fa.IFFOF, 2) = 1
      AND COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= ad.anchor_day
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > ad.anchor_day)
),
incumbent_mgr_asof AS (
    SELECT DISTINCT
        fm.PERSONALCODE,
        fm.INNERCODE
    FROM mf_fundmanagernew fm
    JOIN fof_main_funds ff
      ON ff.INNERCODE = fm.INNERCODE
    JOIN anchor_date ad
      ON 1 = 1
    WHERE fm.POSTNAME = 1
      AND fm.ACCESSIONDATE <= ad.anchor_day
      AND (fm.DIMISSIONDATE IS NULL OR fm.DIMISSIONDATE > ad.anchor_day)
      AND fm.INFOPUBLDATE < DATE_ADD(ad.anchor_day, INTERVAL 1 DAY)
)
SELECT
    pi.CHINESENAME AS `基金经理姓名`,
    ROUND(DATEDIFF(ad.anchor_day, pi.PRACTICEDATE) / 365.25, 2) AS `从业年限`,
    COUNT(DISTINCT im.INNERCODE) AS `在管 FOF 数量`
FROM incumbent_mgr_asof im
JOIN person_info pi
  ON pi.PERSONALCODE = im.PERSONALCODE
 AND pi.rn = 1
JOIN anchor_date ad
  ON 1 = 1
WHERE pi.PRACTICEDATE IS NOT NULL
  AND pi.PRACTICEDATE <= DATE_SUB(ad.anchor_day, INTERVAL 20 YEAR)
GROUP BY im.PERSONALCODE, pi.CHINESENAME, pi.PRACTICEDATE, ad.anchor_day
ORDER BY `在管 FOF 数量` DESC, `从业年限` DESC, `基金经理姓名` ASC;
