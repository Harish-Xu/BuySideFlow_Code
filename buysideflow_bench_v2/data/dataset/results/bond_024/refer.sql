-- name: valuation_rows
WITH params AS (
    SELECT DATE('{{as_of_date}}') AS as_of_date
),
valuation_ranked AS (
    SELECT
        bc.MAINCODE,
        bc.SECUCODE AS bond_code,
        bc.SECUABBR AS bond_name,
        v.YIELDCODE,
        v.CREDIBILITYCODE,
        v.VPYIELD,
        v.TRUEREMAINMATURITY,
        ROW_NUMBER() OVER (
            PARTITION BY bc.MAINCODE, v.YIELDCODE
            ORDER BY
                CASE WHEN v.CREDIBILITYCODE = 1 THEN 0 ELSE 1 END,
                v.INSERTTIME DESC,
                v.ID DESC,
                bc.SECUCODE,
                bc.INNERCODE
        ) AS rn
    FROM bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
    JOIN params p
      ON v.ENDDATE >= p.as_of_date
     AND v.ENDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    JOIN bond_code bc
      ON v.INNERCODE = bc.INNERCODE
    WHERE bc.SECUMARKET = 89
      AND v.CREDIBILITYCODE IN (1, 2)
      AND v.YIELDCODE IN (1, 2)
      AND COALESCE(v.SPECIALMARK, 0) <> 1
      AND v.VPYIELD IS NOT NULL
      AND v.TRUEREMAINMATURITY IS NOT NULL
      AND v.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND (bc.INTERESTENDDATE IS NULL OR DATE(bc.INTERESTENDDATE) >= p.as_of_date)
      AND EXISTS (
          SELECT 1
          FROM bond_conceptnature cn
          WHERE cn.INNERCODE = bc.INNERCODE
            AND cn.CONCEPTTYPE = 40
            AND cn.CONNATURE = 4041
            AND DATE(cn.BEGINDATE) <= p.as_of_date
            AND (cn.ENDDATE IS NULL OR DATE(cn.ENDDATE) > p.as_of_date)
            AND cn.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      )
),
pivoted AS (
    SELECT
        MAINCODE,
        MAX(CASE WHEN YIELDCODE = 1 THEN bond_code END) AS exercise_bond_code,
        MAX(CASE WHEN YIELDCODE = 1 THEN bond_name END) AS exercise_bond_name,
        MAX(CASE WHEN YIELDCODE = 1 THEN VPYIELD END) AS exercise_yield_pct,
        MAX(CASE WHEN YIELDCODE = 1 THEN TRUEREMAINMATURITY END) AS exercise_remaining_years,
        MAX(CASE WHEN YIELDCODE = 2 THEN VPYIELD END) AS maturity_yield_pct,
        MAX(CASE WHEN YIELDCODE = 2 THEN TRUEREMAINMATURITY END) AS maturity_remaining_years
    FROM valuation_ranked
    WHERE rn = 1
    GROUP BY MAINCODE
),
rating_ranked AS (
    SELECT r.MAINCODE, r.CRASCODE, r.CRCODE, r.CRSTATUS,
           r.RATINGEFFECTIVEENDDATE,
           ROW_NUMBER() OVER (
               PARTITION BY r.MAINCODE, r.CRASCODE
               ORDER BY r.INFOPUBLDATE DESC, r.CRDATE DESC, r.ID DESC
           ) AS rn
    FROM bond_bdcreditgrading r
    JOIN pivoted v ON v.MAINCODE = r.MAINCODE
    CROSS JOIN params p
    WHERE r.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND r.CRDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND r.CRTYPECODE = 2 AND r.CRSYSTEM = 1
),
lowest_valid_rating AS (
    SELECT r.MAINCODE, MIN(r.CRCODE) AS rating_code
    FROM rating_ranked r CROSS JOIN params p
    WHERE r.rn = 1 AND r.CRSTATUS = 1
      AND (r.RATINGEFFECTIVEENDDATE IS NULL OR DATE(r.RATINGEFFECTIVEENDDATE) >= p.as_of_date)
    GROUP BY r.MAINCODE
)
SELECT
    exercise_bond_code AS bond_code,
    exercise_bond_name AS bond_name,
    exercise_yield_pct,
    exercise_remaining_years,
    maturity_yield_pct,
    maturity_remaining_years
FROM pivoted
JOIN lowest_valid_rating r ON r.MAINCODE = pivoted.MAINCODE AND r.rating_code = 980
WHERE exercise_yield_pct IS NOT NULL
  AND exercise_remaining_years BETWEEN 1 AND 5
  AND maturity_yield_pct IS NOT NULL
  AND maturity_remaining_years IS NOT NULL;

-- name: treasury_curve
WITH params AS (
    SELECT DATE('{{as_of_date}}') AS as_of_date
),
ranked_curve AS (
    SELECT
        c.YEARSTOMATURITY AS maturity_year,
        c.YIELD * 100.0 AS yield_pct,
        ROW_NUMBER() OVER (
            PARTITION BY c.YEARSTOMATURITY
            ORDER BY c.ID DESC
        ) AS rn
    FROM bond_cbyieldcurve c FORCE INDEX (IX_BOND_CBYIELDCURVE)
    JOIN params p
      ON c.ENDDATE >= p.as_of_date
     AND c.ENDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE c.CURVECODE = 10
      AND c.YIELDTYPECODE = 1
      AND c.YEARSTOMATURITY IS NOT NULL
      AND c.YIELD IS NOT NULL
)
SELECT
    maturity_year,
    yield_pct
FROM ranked_curve
WHERE rn = 1
ORDER BY maturity_year;
