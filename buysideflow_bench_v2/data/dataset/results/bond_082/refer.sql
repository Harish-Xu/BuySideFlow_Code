WITH valuation_sample AS (
    SELECT
        CAST(v.INNERCODE AS UNSIGNED) AS innercode,
        CAST(bc.MAINCODE AS UNSIGNED) AS maincode,
        CAST(bc.COMPANYCODE AS UNSIGNED) AS companycode,
        bc.SECUCODE AS bond_code,
        bc.SECUABBR AS bond_name,
        bc.ISSUER AS issuer,
        DATE(v.ENDDATE) AS valuation_date,
        v.VPYIELD AS valuation_yield_pct,
        v.TRUEREMAINMATURITY AS remain_maturity_years,
        v.CREDIBILITYCODE,
        v.YIELDCODE,
        v.INSERTTIME AS valuation_insert_time,
        v.UPDATETIME AS valuation_update_time,
        v.ID AS valuation_id,
        ROW_NUMBER() OVER (
            PARTITION BY v.INNERCODE
            ORDER BY v.INSERTTIME DESC, v.UPDATETIME DESC, v.ID DESC
        ) AS rn
    FROM bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
    INNER JOIN bond_code bc FORCE INDEX (IX_BOND_CODE)
        ON bc.INNERCODE = v.INNERCODE
    WHERE v.ENDDATE >= DATE('2025-06-30')
      AND v.ENDDATE < DATE('2025-07-01')
      AND v.CREDIBILITYCODE = 1
      AND v.YIELDCODE = 2
      AND v.VPYIELD IS NOT NULL
      AND v.TRUEREMAINMATURITY > 3
      AND v.TRUEREMAINMATURITY <= 5
      AND (v.INSERTTIME IS NULL OR v.INSERTTIME < TIMESTAMP('2025-07-01'))
      AND bc.MAINCODE IS NOT NULL
      AND bc.COMPANYCODE IS NOT NULL
      AND bc.BONDTYPELEVEL1 = 1800
      AND (bc.LISTEDDATE IS NULL OR DATE(bc.LISTEDDATE) <= DATE('2025-06-30'))
      AND (bc.INTERESTENDDATE IS NULL OR DATE(bc.INTERESTENDDATE) >= DATE('2025-06-30'))
      AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) >= DATE('2025-06-30'))
)
SELECT
    innercode,
    maincode,
    companycode,
    bond_code,
    bond_name,
    issuer,
    valuation_date,
    valuation_yield_pct,
    remain_maturity_years,
    CREDIBILITYCODE,
    YIELDCODE,
    valuation_insert_time,
    valuation_update_time,
    valuation_id
FROM valuation_sample
WHERE rn = 1;
