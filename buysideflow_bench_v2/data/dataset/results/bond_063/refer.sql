-- bond_063: portfolio market value, weighted yield/duration/convexity and
-- duration contribution split by Shenwan level-1 industry as of 2025-06-30.
--
-- Field evidence from schema:
--   bond_code.SECUCODE identifies input bond codes and MAINCODE/INNERCODE.
--   bond_cbvaluationall.VALUEFULLPRICE/VPYIELD/VPADURATION/VPCONVEXITY are
--   ChinaBond valuation full price, valuation yield, modified duration and convexity.
--   bond_biindustry stores issuer industry classifications; STANDARD=38 is
--   Shenwan industry classification (new), and FIRSTINDUSTRYNAME is level-1.

WITH holdings AS (
    SELECT '102381543' AS bond_code, 5000.0 AS face_value_wan UNION ALL
    SELECT '102484675' AS bond_code, 3000.0 AS face_value_wan UNION ALL
    SELECT '132580058' AS bond_code, 7000.0 AS face_value_wan UNION ALL
    SELECT '242779' AS bond_code, 4000.0 AS face_value_wan UNION ALL
    SELECT '102581082' AS bond_code, 3000.0 AS face_value_wan UNION ALL
    SELECT '102581962' AS bond_code, 5000.0 AS face_value_wan UNION ALL
    SELECT '1380132' AS bond_code, 6000.0 AS face_value_wan UNION ALL
    SELECT '2380185' AS bond_code, 4000.0 AS face_value_wan UNION ALL
    SELECT '133783' AS bond_code, 3000.0 AS face_value_wan UNION ALL
    SELECT '102300539' AS bond_code, 5000.0 AS face_value_wan
),
matched_code AS (
    SELECT
        h.bond_code,
        h.face_value_wan,
        bc.MAINCODE,
        bc.INNERCODE,
        bc.SECUMARKET,
        bc.SECUCODE,
        bc.SECUABBR,
        bc.COMPANYCODE,
        ROW_NUMBER() OVER (
            PARTITION BY h.bond_code
            ORDER BY
                CASE
                    WHEN bc.SECUMARKET = 89 THEN 0
                    WHEN bc.SECUMARKET = 83 THEN 1
                    WHEN bc.SECUMARKET = 90 THEN 2
                    ELSE 3
                END,
                bc.INNERCODE
        ) AS rn
    FROM holdings h
    LEFT JOIN bond_code bc
      ON bc.SECUCODE = h.bond_code
),
selected_code AS (
    SELECT *
    FROM matched_code
    WHERE rn = 1
),
valuation_ranked AS (
    SELECT
        sc.bond_code,
        v.VALUEFULLPRICE,
        v.VPYIELD,
        v.VPADURATION,
        v.VPCONVEXITY,
        DATE(v.ENDDATE) AS valuation_date,
        ROW_NUMBER() OVER (
            PARTITION BY sc.bond_code
            ORDER BY v.UPDATETIME DESC, v.ID DESC
        ) AS rn
    FROM selected_code sc
    LEFT JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
      ON v.INNERCODE = sc.INNERCODE
     AND v.ENDDATE >= DATE('2025-06-30')
     AND v.ENDDATE < DATE_ADD(DATE('2025-06-30'), INTERVAL 1 DAY)
     AND v.CREDIBILITYCODE = 1
     AND v.YIELDCODE = 2
     AND (v.INSERTTIME IS NULL OR v.INSERTTIME < DATE_ADD(DATE('2025-06-30'), INTERVAL 1 DAY))
     AND v.UPDATETIME < DATE_ADD(DATE('2025-06-30'), INTERVAL 1 DAY)
),
latest_industry AS (
    SELECT
        sc.bond_code,
        i.FIRSTINDUSTRYNAME,
        DATE(i.EFFECTIVEDATE) AS industry_effective_date,
        DATE(i.INFOPUBLDATE) AS industry_info_date,
        ROW_NUMBER() OVER (
            PARTITION BY sc.bond_code
            ORDER BY i.EFFECTIVEDATE DESC, i.INFOPUBLDATE DESC, i.ID DESC
        ) AS rn
    FROM selected_code sc
    LEFT JOIN bond_biindustry i
     ON i.COMPANYCODE = sc.COMPANYCODE
     AND i.STANDARD = 38
     AND i.EFFECTIVEDATE <= DATE('2025-06-30')
     AND i.INFOPUBLDATE < DATE_ADD(DATE('2025-06-30'), INTERVAL 1 DAY)
     AND (i.CANCELDATE IS NULL OR i.CANCELDATE > DATE('2025-06-30'))
     AND i.FIRSTINDUSTRYNAME IS NOT NULL
)
SELECT
    sc.bond_code,
    sc.face_value_wan,
    sc.MAINCODE,
    sc.INNERCODE,
    sc.SECUMARKET,
    sc.SECUABBR,
    sc.COMPANYCODE,
    v.valuation_date,
    v.VALUEFULLPRICE,
    v.VPYIELD,
    v.VPADURATION,
    v.VPCONVEXITY,
    i.FIRSTINDUSTRYNAME,
    i.industry_effective_date,
    i.industry_info_date
FROM selected_code sc
LEFT JOIN valuation_ranked v
  ON v.bond_code = sc.bond_code
 AND v.rn = 1
LEFT JOIN latest_industry i
  ON i.bond_code = sc.bond_code
 AND i.rn = 1
ORDER BY FIELD(
    sc.bond_code,
    '102381543',
    '102484675',
    '132580058',
    '242779',
    '102581082',
    '102581962',
    '1380132',
    '2380185',
    '133783',
    '102300539'
);
