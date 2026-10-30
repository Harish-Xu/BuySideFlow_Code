-- Extracted from fund_104/refer.py (main SQL: FUND_POOL_SQL)
-- Template: {{ name }} will be substituted from refer.py before execution.
-- DO NOT change the variable names here without updating refer.py.

WITH type_asof AS (
    SELECT t.INNERCODE, t.SECASSETCATCODE,
           ROW_NUMBER() OVER (
               PARTITION BY t.INNERCODE
               ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
           ) AS rn
    FROM mf_jyfundtype t
    WHERE t.STANDARD = 75 AND t.EFFECTIVEDATE <= '{{ AS_OF_DATE }}'
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > '{{ AS_OF_DATE }}')
      AND t.INSERTTIME < '{{ VISIBLE_CUTOFF }}'
)
SELECT fa.INNERCODE, sm.SECUCODE, sm.SECUABBR
FROM mf_fundarchives fa
JOIN secumain sm ON sm.INNERCODE = fa.INNERCODE AND sm.SECUCATEGORY = 8
JOIN type_asof t ON t.INNERCODE = fa.INNERCODE AND t.rn = 1
WHERE t.SECASSETCATCODE = 1301
  AND fa.TYPE IN (2, 3, 4, 7, 8)
  AND fa.MAINCODE = fa.SECURITYCODE
  AND fa.ESTABLISHMENTDATE < '{{ START_DATE }}'
  AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > '{{ AS_OF_DATE }}')
