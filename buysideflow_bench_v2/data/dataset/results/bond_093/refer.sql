WITH holdings AS (
    SELECT '102381543' AS bond_code, 5000.0 AS face_value_wan UNION ALL
    SELECT '132580058' AS bond_code, 7000.0 AS face_value_wan UNION ALL
    SELECT '102581962' AS bond_code, 5000.0 AS face_value_wan UNION ALL
    SELECT '2380185' AS bond_code, 4000.0 AS face_value_wan UNION ALL
    SELECT '102300539' AS bond_code, 5000.0 AS face_value_wan
),
params AS (
    SELECT
        DATE('2025-03-31') AS start_date,
        DATE('2025-06-30') AS end_date,
        TIMESTAMP('2025-04-01 00:00:00') AS start_next_day_ts,
        TIMESTAMP('2025-07-01 00:00:00') AS end_next_day_ts
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
     AND (bc.UPDATETIME IS NULL OR bc.UPDATETIME < (SELECT end_next_day_ts FROM params))
),
selected_code AS (
    SELECT *
    FROM matched_code
    WHERE rn = 1
),
start_valuation AS (
    SELECT *
    FROM (
        SELECT
            sc.bond_code,
            v.VALUEFULLPRICE AS start_full_price,
            v.VPYIELD AS start_yield_pct,
            v.VPADURATION AS start_modified_duration,
            v.VPCONVEXITY AS start_convexity,
            v.TRUEREMAINMATURITY AS start_remaining_years,
            v.UPDATETIME AS start_valuation_update_time,
            ROW_NUMBER() OVER (
                PARTITION BY sc.bond_code
                ORDER BY v.UPDATETIME DESC, v.ID DESC
            ) AS rn
        FROM selected_code sc
        CROSS JOIN params p
        LEFT JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
          ON v.INNERCODE = sc.INNERCODE
         AND v.ENDDATE = p.start_date
         AND v.CREDIBILITYCODE = 1
         AND v.YIELDCODE = 2
         AND (v.UPDATETIME IS NULL OR v.UPDATETIME < p.start_next_day_ts)
    ) ranked
    WHERE rn = 1
),
end_valuation AS (
    SELECT *
    FROM (
        SELECT
            sc.bond_code,
            v.VALUEFULLPRICE AS end_full_price,
            v.VPYIELD AS end_yield_pct,
            v.VPADURATION AS end_modified_duration,
            v.VPCONVEXITY AS end_convexity,
            v.TRUEREMAINMATURITY AS end_remaining_years,
            v.UPDATETIME AS end_valuation_update_time,
            ROW_NUMBER() OVER (
                PARTITION BY sc.bond_code
                ORDER BY v.UPDATETIME DESC, v.ID DESC
            ) AS rn
        FROM selected_code sc
        CROSS JOIN params p
        LEFT JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
          ON v.INNERCODE = sc.INNERCODE
         AND v.ENDDATE = p.end_date
         AND v.CREDIBILITYCODE = 1
         AND v.YIELDCODE = 2
         AND (v.UPDATETIME IS NULL OR v.UPDATETIME < p.end_next_day_ts)
    ) ranked
    WHERE rn = 1
)
SELECT
    sc.bond_code,
    sc.face_value_wan,
    sc.MAINCODE AS maincode,
    sc.INNERCODE AS innercode,
    sc.SECUABBR AS bond_name,
    sv.start_full_price,
    sv.start_yield_pct,
    sv.start_modified_duration,
    sv.start_convexity,
    sv.start_remaining_years,
    sv.start_valuation_update_time,
    ev.end_full_price,
    ev.end_yield_pct,
    ev.end_modified_duration,
    ev.end_convexity,
    ev.end_remaining_years,
    ev.end_valuation_update_time
FROM selected_code sc
LEFT JOIN start_valuation sv
  ON sv.bond_code = sc.bond_code
LEFT JOIN end_valuation ev
  ON ev.bond_code = sc.bond_code
ORDER BY FIELD(sc.bond_code, '102381543', '132580058', '102581962', '2380185', '102300539');
