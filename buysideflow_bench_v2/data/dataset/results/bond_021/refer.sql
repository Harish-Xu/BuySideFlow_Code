WITH latest_ts AS (
    SELECT
        MAX(ENDDATE) AS latest_enddate
    FROM bond_cbvaluationall FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
    WHERE ENDDATE <= '2024-12-31'
      AND VPYIELD IS NOT NULL
      AND TRUEYIELD IS NOT NULL
),
day_snap AS (
    SELECT
        v.INNERCODE,
        v.VPYIELD,
        v.TRUEYIELD,
        v.TRUEREMAINMATURITY,
        ROW_NUMBER() OVER (
            PARTITION BY v.INNERCODE
            ORDER BY
                CASE WHEN v.CREDIBILITYCODE = 1 THEN 0 ELSE 1 END,
                v.ID DESC
        ) AS rn
    FROM bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
    JOIN latest_ts t
      ON v.ENDDATE = t.latest_enddate
    WHERE v.VPYIELD IS NOT NULL
      AND v.TRUEYIELD IS NOT NULL
)
SELECT
    bc.SECUABBR AS bond_name,
    bc.ISSUER,
    ROUND(v.VPYIELD, 4) AS valuation_yield_pct,
    ROUND(v.TRUEYIELD, 4) AS true_yield_pct,
    ROUND(ABS(v.VPYIELD - v.TRUEYIELD) * 100, 2) AS abs_deviation_bp,
    ROUND(v.TRUEREMAINMATURITY, 4) AS remain_years
FROM day_snap v
JOIN bond_code bc
  ON v.INNERCODE = bc.INNERCODE
JOIN latest_ts t
WHERE v.rn = 1
  AND (bc.INTERESTENDDATE IS NULL OR bc.INTERESTENDDATE >= t.latest_enddate)
  AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) > DATE(t.latest_enddate))
ORDER BY abs_deviation_bp DESC, bc.SECUCODE
LIMIT 30;
