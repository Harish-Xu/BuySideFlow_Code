WITH
base_code AS (
    SELECT DISTINCT
        bc.MAINCODE,
        bc.INNERCODE,
        bc.SECUMARKET,
        DATEDIFF(DATE(bc.INTERESTENDDATE), DATE('2025-11-26')) / 365 AS fallback_remaining_years
    FROM bond_code bc
    WHERE bc.MAINCODE IS NOT NULL
      AND bc.INNERCODE IS NOT NULL
      AND bc.CURRENCY = 1420
      AND bc.LISTEDDATE IS NOT NULL
      AND DATE(bc.LISTEDDATE) <= DATE('2025-11-26')
      AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) > DATE('2025-11-26'))
      AND (
          bc.BONDTYPELEVEL1 IN (1600, 1700, 1800, 2500, 2600, 2800, 2900, 3100, 3200, 3300, 3700)
          OR bc.BONDTYPELEVEL2 IN (1901, 1903)
      )
),
valuation_ranked AS (
    SELECT
        v.INNERCODE,
        v.TRUEREMAINMATURITY,
        ROW_NUMBER() OVER (
            PARTITION BY v.INNERCODE
            ORDER BY
                CASE WHEN v.CREDIBILITYCODE = 1 THEN 0 ELSE 1 END,
                v.INSERTTIME DESC,
                v.UPDATETIME DESC,
                v.ID DESC
        ) AS rn
    FROM bond_cbvaluationall v
    JOIN (SELECT DISTINCT INNERCODE FROM base_code) b
      ON b.INNERCODE = v.INNERCODE
    WHERE v.ENDDATE >= DATE('2025-11-26')
      AND v.ENDDATE < DATE_ADD(DATE('2025-11-26'), INTERVAL 1 DAY)
      AND v.YIELDCODE = 2
      AND v.TRUEREMAINMATURITY IS NOT NULL
      AND (v.INSERTTIME IS NULL OR v.INSERTTIME < DATE_ADD(DATE('2025-11-26'), INTERVAL 1 DAY))
),
valuation_one AS (
    SELECT
        INNERCODE,
        TRUEREMAINMATURITY
    FROM valuation_ranked
    WHERE rn = 1
)
SELECT DISTINCT
    b.MAINCODE,
    b.INNERCODE,
    b.SECUMARKET,
    COALESCE(v.TRUEREMAINMATURITY, b.fallback_remaining_years) AS remaining_years
FROM base_code b
LEFT JOIN valuation_one v
  ON v.INNERCODE = b.INNERCODE
WHERE COALESCE(v.TRUEREMAINMATURITY, b.fallback_remaining_years) BETWEEN 1 AND 5;
