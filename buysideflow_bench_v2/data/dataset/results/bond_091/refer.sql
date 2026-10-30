WITH params AS (
    SELECT
        DATE('2025-09-29') AS as_of_date,
        TIMESTAMP('2025-09-30 00:00:00') AS next_day_ts
),
valuation_ranked AS (
    SELECT
        bc.MAINCODE,
        bc.INNERCODE,
        bc.SECUCODE AS bond_code,
        bc.SECUABBR AS bond_name,
        bc.ISSUER AS issuer,
        bc.CHINAME AS full_name_from_code,
        bi.BONDFULLNAME AS bond_full_name,
        bi.BASICASSETTYPE AS basic_asset_type_code,
        bi.ABSLEVEL,
        bi.ABSLEVELY,
        v.VPYIELD AS valuation_yield_pct,
        v.TRUEREMAINMATURITY AS remaining_years,
        ROW_NUMBER() OVER (
            PARTITION BY bc.MAINCODE
            ORDER BY
                v.ENDDATE DESC,
                CASE WHEN bc.SECUMARKET = 89 THEN 0 ELSE 1 END,
                COALESCE(bc.SECUCODE, ''),
                bc.INNERCODE,
                v.ID DESC
        ) AS rn
    FROM params p
    JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
      ON v.ENDDATE = p.as_of_date
    JOIN bond_code bc
      ON v.INNERCODE = bc.INNERCODE
    JOIN bond_basicinfo bi
      ON bc.MAINCODE = bi.MAINCODE
    WHERE v.CREDIBILITYCODE = 1
      AND v.YIELDCODE = 2
      AND v.VPYIELD IS NOT NULL
      AND v.TRUEREMAINMATURITY IS NOT NULL
      AND v.TRUEREMAINMATURITY > 0
      AND (v.UPDATETIME IS NULL OR v.UPDATETIME < p.next_day_ts)
      AND bc.BONDTYPELEVEL1 = 2000
      AND bi.BASICASSETTYPE IN (14, 18, 19, 22)
      AND bi.ABSLEVEL = 12
      AND bc.MAINCODE IS NOT NULL
      AND bc.INNERCODE IS NOT NULL
      AND (bc.LISTEDDATE IS NULL OR bc.LISTEDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
      AND (bc.INTERESTENDDATE IS NULL OR bc.INTERESTENDDATE >= p.as_of_date)
      AND (bc.DELISTDATE IS NULL OR bc.DELISTDATE >= p.as_of_date)
      AND (bc.UPDATETIME IS NULL OR bc.UPDATETIME < p.next_day_ts)
      AND (bi.INITIALINFOPUBLDATE IS NULL OR bi.INITIALINFOPUBLDATE <= p.as_of_date)
)
SELECT
    p.as_of_date AS valuation_date,
    CAST(v.MAINCODE AS UNSIGNED) AS maincode,
    CAST(v.INNERCODE AS UNSIGNED) AS innercode,
    v.bond_code,
    v.bond_name,
    v.issuer,
    COALESCE(v.bond_full_name, v.full_name_from_code, v.bond_name) AS bond_full_name,
    CAST(v.basic_asset_type_code AS SIGNED) AS basic_asset_type_code,
    CAST(v.ABSLEVEL AS SIGNED) AS abs_level,
    CAST(v.ABSLEVELY AS SIGNED) AS abs_level_y,
    ROUND(v.remaining_years, 6) AS remaining_years,
    ROUND(v.valuation_yield_pct, 6) AS valuation_yield_pct
FROM params p
JOIN valuation_ranked v
  ON v.rn = 1
ORDER BY v.MAINCODE;
