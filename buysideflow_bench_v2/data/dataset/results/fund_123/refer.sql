WITH surviving_main_shares AS (
    SELECT fa.INNERCODE, fa.SECURITYCODE AS fund_code,
           COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, fa.SECURITYCODE) AS fund_name
    FROM mf_fundarchives fa
    LEFT JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE AND sm.SECUCATEGORY = 8
    WHERE fa.MAINCODE = fa.SECURITYCODE AND fa.MAINCODE <> ''
      AND fa.TYPE IN (2, 3, 4, 7, 8)
      AND fa.ESTABLISHMENTDATE < '2026-01-01'
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= '2025-12-31')
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND fa.INVESTMENTTYPE = 16
), ranked_types AS (
    SELECT t.INNERCODE, t.SECASSETCATCODE, t.CANCELDATE,
           ROW_NUMBER() OVER (
               PARTITION BY t.INNERCODE
               ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
           ) AS version_rank
    FROM mf_jyfundtype t
    JOIN surviving_main_shares f ON f.INNERCODE = t.INNERCODE
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE < '2026-01-01'
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > '2025-12-31')
      AND t.INSERTTIME < '2026-01-01'
), fund_pool AS (
    SELECT f.*
    FROM surviving_main_shares f
    JOIN ranked_types t ON t.INNERCODE = f.INNERCODE
    WHERE t.version_rank = 1
      AND t.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
), scale_versions AS (
    SELECT s.INNERCODE, DATE(s.ENDDATE) AS scale_date, s.COMBNVI,
           ROW_NUMBER() OVER (
               PARTITION BY s.INNERCODE, s.ENDDATE
               ORDER BY s.INSERTTIME DESC, s.ID DESC
           ) AS version_rank
    FROM mf_scaleanalysis s
    JOIN fund_pool f ON f.INNERCODE = s.INNERCODE
    WHERE s.ENDDATE < '2026-01-01'
      AND s.INSERTTIME < '2026-01-01'
), ranked_scale_periods AS (
    SELECT s.INNERCODE, s.scale_date, s.COMBNVI,
           ROW_NUMBER() OVER (
               PARTITION BY s.INNERCODE ORDER BY s.scale_date DESC
           ) AS period_rank
    FROM scale_versions s
    WHERE s.version_rank = 1
)
SELECT f.INNERCODE, f.fund_code, f.fund_name, s.scale_date, s.COMBNVI
FROM fund_pool f
JOIN ranked_scale_periods s ON s.INNERCODE = f.INNERCODE
WHERE s.period_rank = 1
  AND s.COMBNVI IS NOT NULL AND s.COMBNVI >= 200000000
ORDER BY f.fund_code;
