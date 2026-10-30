WITH params AS (
    SELECT
        DATE('2021-09-22') AS as_of_date,
        DATE('2021-09-23') AS cutoff_time
),
fund_pool AS (
    SELECT
        fa.INNERCODE,
        sm.SECUCODE AS fund_code,
        sm.SECUABBR AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    CROSS JOIN params p
    WHERE fa.FUNDNATURE = 2
      AND fa.TYPE IN (2, 3, 4, 7, 8)
      AND fa.FLOATTYPE IN (2, 3)
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE < p.cutoff_time
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > p.as_of_date)
)
SELECT
    fp.fund_code AS `基金代码`,
    fp.fund_name AS `基金简称`
FROM fund_pool fp
CROSS JOIN params p
WHERE EXISTS (
    SELECT 1
    FROM mf_benchmarkallocation ba
    WHERE ba.INNERCODE = fp.INNERCODE
      AND ba.TRACEDINDEXCODE = 3205
      AND ba.EXCUTEDATE < p.cutoff_time
      AND (ba.CANCELDATE IS NULL OR ba.CANCELDATE > p.as_of_date)
      AND ba.INFOPUBLDATE < p.cutoff_time
)
ORDER BY fp.fund_code ASC;
