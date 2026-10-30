WITH params AS (
    SELECT
        DATE('2026-02-16') AS cohort_date,
        DATE('2025-06-30') AS start_scale_date,
        DATE('2025-12-31') AS end_scale_date,
        TIMESTAMP('2026-02-17 00:00:00') AS visible_cutoff
),
type_ranked AS (
    SELECT
        t.INNERCODE,
        t.THIRDASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS rn
    FROM mf_jyfundtype t FORCE INDEX (IX_MF_JYFUNDTYPE)
    JOIN params p
      ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= p.cohort_date
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.cohort_date)
      AND t.INSERTTIME < p.visible_cutoff
),
fund_pool AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, fa.SECURITYCODE) AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN type_ranked tr
      ON tr.INNERCODE = fa.INNERCODE
     AND tr.rn = 1
    JOIN params p
      ON 1 = 1
    WHERE tr.THIRDASSETCATCODE = 130202
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= p.cohort_date
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > p.cohort_date)
),
scale_ranked AS (
    SELECT
        fp.INNERCODE,
        fp.fund_code,
        fp.fund_name,
        s.ENDDATE AS scale_date,
        s.COMBNVI AS combined_scale,
        ROW_NUMBER() OVER (
            PARTITION BY fp.INNERCODE, s.ENDDATE
            ORDER BY s.INSERTTIME DESC, s.ID DESC
        ) AS rn
    FROM fund_pool fp
    JOIN mf_scaleanalysis s FORCE INDEX (IX_MF_SCALEANALYSIS)
      ON s.INNERCODE = fp.INNERCODE
    JOIN params p
      ON s.ENDDATE IN (p.start_scale_date, p.end_scale_date)
    WHERE s.INSERTTIME < p.visible_cutoff
),
scale_pivot AS (
    SELECT
        sr.INNERCODE,
        sr.fund_code,
        sr.fund_name,
        MAX(CASE WHEN sr.scale_date = p.start_scale_date AND sr.rn = 1 THEN sr.combined_scale END) AS start_scale,
        MAX(CASE WHEN sr.scale_date = p.end_scale_date AND sr.rn = 1 THEN sr.combined_scale END) AS end_scale
    FROM scale_ranked sr
    JOIN params p
      ON 1 = 1
    GROUP BY sr.INNERCODE, sr.fund_code, sr.fund_name
),
ranked AS (
    SELECT
        fund_code,
        fund_name,
        start_scale / 100000000.0 AS start_scale_100m,
        end_scale / 100000000.0 AS end_scale_100m,
        (end_scale / start_scale - 1.0) * 100.0 AS scale_growth_pct
    FROM scale_pivot
    WHERE start_scale >= 1000000000
      AND end_scale >= 0
)
SELECT
    fund_code,
    fund_name,
    start_scale_100m,
    end_scale_100m,
    scale_growth_pct
FROM ranked
ORDER BY scale_growth_pct DESC, fund_code ASC
LIMIT 10;
