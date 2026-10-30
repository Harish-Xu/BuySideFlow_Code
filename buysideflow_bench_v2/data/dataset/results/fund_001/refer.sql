WITH params AS (
    SELECT DATE('2026-02-13') AS as_of_date
)
SELECT
    fa.SECURITYCODE AS `基金代码`,
    sm.SECUABBR AS `基金简称`,
    m.NAME AS `基金经理`,
    DATE_FORMAT(m.ACCESSIONDATE, '%Y-%m-%d') AS `开始管理日期`,
    DATE_FORMAT(
        CASE
            WHEN m.DIMISSIONDATE <= p.as_of_date THEN m.DIMISSIONDATE
            ELSE NULL
        END,
        '%Y-%m-%d'
    ) AS `离任日期`
FROM params p
JOIN mf_fundarchives fa
  ON fa.SECURITYCODE = '005847'
 AND COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
JOIN secumain sm
  ON sm.INNERCODE = fa.INNERCODE
 AND sm.SECUCATEGORY = 8
JOIN mf_fundmanagernew m
  ON m.INNERCODE = fa.INNERCODE
WHERE m.POSTNAME = 1
  AND m.ACCESSIONDATE <= p.as_of_date
  AND (
      m.INFOPUBLDATE IS NULL
      OR m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
  )
ORDER BY m.ACCESSIONDATE ASC, m.NAME ASC;
