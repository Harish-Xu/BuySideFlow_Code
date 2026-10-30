WITH report_params AS (
    SELECT
        DATE('2025-09-30') AS q3_date,
        DATE('2025-12-31') AS q4_date,
        DATE('2026-03-31') AS data_cutoff
),
latest_type AS (
    SELECT
        t.INNERCODE,
        t.SECASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS rn
    FROM mf_jyfundtype t
    JOIN report_params rp
      ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= rp.q4_date
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > rp.q4_date)
      AND t.INSERTTIME < DATE_ADD(rp.data_cutoff, INTERVAL 1 DAY)
),
fund_pool AS (
    SELECT
        fa.INNERCODE,
        fs.SECUCODE AS fund_code,
        fs.SECUABBR AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain fs
      ON fs.INNERCODE = fa.INNERCODE
    JOIN latest_type lt
      ON lt.INNERCODE = fa.INNERCODE
     AND lt.rn = 1
    JOIN report_params rp
      ON 1 = 1
    WHERE fs.SECUCATEGORY = 8
      AND lt.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
      AND fa.MAINCODE = fa.SECURITYCODE
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND fa.INVESTMENTTYPE = 16
      AND fa.ESTABLISHMENTDATE <= rp.q4_date
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > rp.q4_date)
),
q3_holdings AS (
    SELECT
        k.INNERCODE,
        k.STOCKINNERCODE,
        k.RATIOINNV AS ratio_in_nv
    FROM mf_keystockportfolio k
    JOIN fund_pool fp
      ON fp.INNERCODE = k.INNERCODE
    JOIN report_params rp
      ON DATE(k.REPORTDATE) = rp.q3_date
    WHERE k.SERIALNUMBER <= 10
      AND k.INFOPUBLDATE < DATE_ADD(rp.data_cutoff, INTERVAL 1 DAY)
),
q4_holdings AS (
    SELECT DISTINCT
        k.INNERCODE,
        k.STOCKINNERCODE
    FROM mf_keystockportfolio k
    JOIN fund_pool fp
      ON fp.INNERCODE = k.INNERCODE
    JOIN report_params rp
      ON DATE(k.REPORTDATE) = rp.q4_date
    WHERE k.SERIALNUMBER <= 10
      AND k.INFOPUBLDATE < DATE_ADD(rp.data_cutoff, INTERVAL 1 DAY)
),
q4_reports AS (
    SELECT
        a.INNERCODE, a.MVOFSTOCK, a.MVOFEQUITY, a.TOTALASSET,
        a.MVOFBOND, a.MVOFMONETARY, a.MVOFRETURNSALE, a.MVOFOTHERI,
        ROW_NUMBER() OVER (
            PARTITION BY a.INNERCODE
            ORDER BY a.INFOPUBLDATE DESC, a.ID DESC
        ) AS rn
    FROM mf_assetallocationnew a
    JOIN fund_pool fp ON fp.INNERCODE = a.INNERCODE
    JOIN report_params rp ON a.REPORTDATE = rp.q4_date
    WHERE a.INFOPUBLDATE < DATE_ADD(rp.data_cutoff, INTERVAL 1 DAY)
),
q4_coverage AS (
    SELECT
        f.INNERCODE,
        CASE
            WHEN EXISTS (
                SELECT 1 FROM q4_holdings h WHERE h.INNERCODE = f.INNERCODE
            ) THEN 'holdings'
            WHEN a.MVOFSTOCK = 0 THEN 'zero_stock'
            -- NULL stock assets alone do not mean zero. These non-overlapping
            -- non-stock assets must fully reconcile to the positive asset total.
            WHEN a.MVOFSTOCK IS NULL
             AND (a.MVOFEQUITY IS NULL OR a.MVOFEQUITY = 0)
             AND a.TOTALASSET > 0
             AND a.TOTALASSET = COALESCE(a.MVOFBOND, 0)
                              + COALESCE(a.MVOFMONETARY, 0)
                              + COALESCE(a.MVOFRETURNSALE, 0)
                              + COALESCE(a.MVOFOTHERI, 0)
            THEN 'zero_stock'
            ELSE 'unknown'
        END AS coverage
    FROM (SELECT DISTINCT INNERCODE FROM q3_holdings) f
    LEFT JOIN q4_reports a ON a.INNERCODE = f.INNERCODE AND a.rn = 1
),
full_exit AS (
    SELECT
        q3.INNERCODE,
        COUNT(*) AS full_exit_count,
        SUM(q3.ratio_in_nv) AS full_exit_ratio
    FROM q3_holdings q3
    JOIN q4_coverage f4
      ON f4.INNERCODE = q3.INNERCODE
     AND f4.coverage <> 'unknown'
    LEFT JOIN q4_holdings q4
      ON q4.INNERCODE = q3.INNERCODE
     AND q4.STOCKINNERCODE = q3.STOCKINNERCODE
    WHERE q4.STOCKINNERCODE IS NULL
    GROUP BY q3.INNERCODE
)
SELECT
    fp.fund_code,
    fp.fund_name,
    fe.full_exit_count,
    ROUND(fe.full_exit_ratio * 100, 2) AS full_exit_ratio_pct
FROM full_exit fe
JOIN fund_pool fp
  ON fp.INNERCODE = fe.INNERCODE
ORDER BY fe.full_exit_count DESC, fe.full_exit_ratio DESC, fp.fund_code ASC
LIMIT 10;
