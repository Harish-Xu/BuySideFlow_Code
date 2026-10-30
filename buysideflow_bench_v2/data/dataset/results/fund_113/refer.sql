WITH params AS (
    SELECT
        DATE('2026-02-28') AS as_of_date,
        TIMESTAMP('2026-03-01 00:00:00') AS as_of_next_day
),
fund_candidates AS (
    SELECT fa.INNERCODE, fa.ESTABLISHMENTDATE, fa.FUNDTYPECODE
    FROM mf_fundarchives fa
    JOIN mf_investadvisoroutline ia
      ON ia.INVESTADVISORCODE = fa.INVESTADVISORCODE
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN params p
      ON 1 = 1
    WHERE ia.INVESTADVISORNAME = '交银施罗德基金管理有限公司'
      AND fa.TYPE IN (2, 3, 4, 7, 8)
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= p.as_of_date
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > p.as_of_date)
),
fund_classification AS (
    SELECT
        jt.INNERCODE,
        jt.FIRSTASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY jt.INNERCODE
            ORDER BY jt.EFFECTIVEDATE DESC, jt.INSERTTIME DESC, jt.ID DESC
        ) AS rn
    FROM mf_jyfundtype jt
    JOIN fund_candidates fc ON fc.INNERCODE = jt.INNERCODE
    JOIN params p ON 1 = 1
    WHERE jt.STANDARD = 75
      AND jt.EFFECTIVEDATE <= p.as_of_date
      AND (jt.CANCELDATE IS NULL OR jt.CANCELDATE > p.as_of_date)
      AND jt.INSERTTIME < p.as_of_next_day
),
fund_pool AS (
    SELECT fc.INNERCODE, fc.ESTABLISHMENTDATE
    FROM fund_candidates fc
    LEFT JOIN fund_classification jt
      ON jt.INNERCODE = fc.INNERCODE AND jt.rn = 1
    WHERE jt.FIRSTASSETCATCODE = 12
       OR (jt.INNERCODE IS NULL AND fc.FUNDTYPECODE = 1103)
),
benchmark_version AS (
    SELECT
        ba.INNERCODE,
        DATE(ba.EXCUTEDATE) AS execute_date,
        MD5(GROUP_CONCAT(
            CONCAT(
                COALESCE(CAST(ba.TRACEDINDEXCODE AS CHAR), 'NULL'), ':',
                COALESCE(CAST(ROUND(ba.MINIMUMWEIGHT, 6) AS CHAR), 'NULL'), ':',
                COALESCE(CAST(ROUND(ba.TOPWEIGHT, 6) AS CHAR), 'NULL'), ':',
                COALESCE(CAST(ba.OPERATIONMARK AS CHAR), 'NULL'), ':',
                COALESCE(CAST(ROUND(ba.CONSTANT, 6) AS CHAR), 'NULL'), ':',
                COALESCE(CAST(ba.IFAFTERTAX AS CHAR), 'NULL'), ':',
                COALESCE(CAST(ba.IFCOMPOUND AS CHAR), 'NULL'), ':',
                COALESCE(CAST(ba.EXCHANGERATEINNERCODE AS CHAR), 'NULL')
            )
            ORDER BY ba.TRACEDINDEXCODE, ba.MINIMUMWEIGHT, ba.TOPWEIGHT,
                     ba.OPERATIONMARK, ba.CONSTANT, ba.IFAFTERTAX,
                     ba.IFCOMPOUND, ba.EXCHANGERATEINNERCODE SEPARATOR '|'
        )) AS benchmark_signature
    FROM mf_benchmarkallocation ba
    JOIN fund_pool fp
      ON fp.INNERCODE = ba.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE ba.EXCUTEDATE IS NOT NULL
      AND ba.EXCUTEDATE < p.as_of_next_day
      AND ba.INFOPUBLDATE < p.as_of_next_day
      AND ba.INSERTTIME < p.as_of_next_day
    GROUP BY ba.INNERCODE, DATE(ba.EXCUTEDATE)
),
signature_change AS (
    SELECT
        bv.INNERCODE,
        bv.execute_date,
        bv.benchmark_signature,
        LAG(bv.benchmark_signature) OVER (
            PARTITION BY bv.INNERCODE
            ORDER BY bv.execute_date
        ) AS prev_signature
    FROM benchmark_version bv
),
change_stats AS (
    SELECT
        sc.INNERCODE,
        SUM(CASE WHEN sc.execute_date >= fp.ESTABLISHMENTDATE
                      AND sc.prev_signature IS NOT NULL
                      AND sc.benchmark_signature <> sc.prev_signature THEN 1 ELSE 0 END) AS change_count,
        MAX(CASE WHEN sc.execute_date >= fp.ESTABLISHMENTDATE
                      AND sc.prev_signature IS NOT NULL
                      AND sc.benchmark_signature <> sc.prev_signature THEN sc.execute_date END) AS last_change_date
    FROM signature_change sc
    JOIN fund_pool fp ON fp.INNERCODE = sc.INNERCODE
    GROUP BY sc.INNERCODE
),
ranked AS (
    SELECT
        sm.SECUCODE AS fund_code,
        sm.SECUABBR AS fund_name,
        cs.change_count,
        cs.last_change_date,
        RANK() OVER (
            ORDER BY cs.change_count DESC, cs.last_change_date DESC
        ) AS rk
    FROM change_stats cs
    JOIN secumain sm
      ON sm.INNERCODE = cs.INNERCODE
    WHERE cs.change_count > 0
)
SELECT
    fund_code AS `主基金代码`,
    fund_name AS `基金简称`,
    change_count AS `变更次数`,
    DATE_FORMAT(last_change_date, '%Y-%m-%d') AS `最近一次变更生效日`
FROM ranked
WHERE rk <= 5
ORDER BY change_count DESC, last_change_date DESC, fund_code ASC;
