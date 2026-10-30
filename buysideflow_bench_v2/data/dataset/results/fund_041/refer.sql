WITH params AS (
    SELECT
        DATE('2025-12-24') AS as_of_date,
        TIMESTAMP('2025-12-25 00:00:00') AS visible_cutoff
),
target_manager AS (
    SELECT PERSONALCODE, CHINESENAME
    FROM mf_personalinfo
    WHERE CHINESENAME = '王思岳'
)
SELECT
    tm.CHINESENAME AS `基金经理姓名`,
    ia.INVESTADVISORABBRNAME AS `管理人简称`,
    fa.SECURITYCODE AS `主基金代码`,
    COALESCE(NULLIF(s.SECUABBR, ''), NULLIF(s.CHINAMEABBR, ''), s.CHINAME) AS `基金简称`,
    DATE_FORMAT(m.ACCESSIONDATE, '%Y-%m-%d') AS `开始管理日期`
FROM mf_fundmanagernew m
JOIN target_manager tm
  ON tm.PERSONALCODE = m.PERSONALCODE
JOIN mf_fundarchives fa
  ON fa.INNERCODE = m.INNERCODE
JOIN mf_investadvisoroutline ia
  ON ia.INVESTADVISORCODE = fa.INVESTADVISORCODE
JOIN secumain s
  ON s.INNERCODE = fa.INNERCODE
 AND s.SECUCATEGORY = 8
JOIN params p
  ON m.ACCESSIONDATE <= p.as_of_date
 AND (m.DIMISSIONDATE IS NULL OR m.DIMISSIONDATE > p.as_of_date)
 AND m.INFOPUBLDATE < p.visible_cutoff
 AND fa.ESTABLISHMENTDATE <= p.as_of_date
 AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > p.as_of_date)
WHERE m.POSTNAME = 1
  AND fa.INVESTADVISORCODE = 176497
  AND fa.MAINCODE = fa.SECURITYCODE
ORDER BY fa.SECURITYCODE ASC;
