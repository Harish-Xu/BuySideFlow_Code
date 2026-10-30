WITH params AS (
    SELECT DATE('2025-05-31') AS as_of_date
),
valuation_day AS (
    SELECT
        v.ENDDATE AS valuation_date
    FROM params p
    JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
    WHERE v.ENDDATE <= p.as_of_date
      AND v.CREDIBILITYCODE = 1
      AND v.YIELDCODE = 2
      AND v.VPYIELD IS NOT NULL
      AND (v.INSERTTIME IS NULL OR v.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
      AND v.UPDATETIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    ORDER BY v.ENDDATE DESC
    LIMIT 1
),
valuation_candidates AS (
    SELECT
        bc.MAINCODE,
        bc.COMPANYCODE,
        v.VPYIELD,
        ROW_NUMBER() OVER (
            PARTITION BY bc.MAINCODE
            ORDER BY v.INSERTTIME DESC, v.ID DESC, bc.INNERCODE
        ) AS rn_main
    FROM params p
    CROSS JOIN valuation_day vd
    JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
     ON v.ENDDATE = vd.valuation_date
     AND v.CREDIBILITYCODE = 1
     AND v.YIELDCODE = 2
     AND v.VPYIELD IS NOT NULL
     AND (v.INSERTTIME IS NULL OR v.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
     AND v.UPDATETIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    JOIN bond_code bc
      ON bc.INNERCODE = v.INNERCODE
    WHERE bc.BONDTYPELEVEL1 = 1800
      AND (bc.INTERESTENDDATE IS NULL OR bc.INTERESTENDDATE >= p.as_of_date)
      AND (bc.DELISTDATE IS NULL OR bc.DELISTDATE >= p.as_of_date)
),
sample_bonds AS (
    SELECT
        MAINCODE,
        COMPANYCODE,
        VPYIELD
    FROM valuation_candidates
    WHERE rn_main = 1
),
latest_audit AS (
    SELECT
        a.COMPANYCODE,
        CASE WHEN a.OPINIONTYPE = 1 THEN '标准无保留' ELSE '非标' END AS audit_group,
        ROW_NUMBER() OVER (
            PARTITION BY a.COMPANYCODE
            ORDER BY a.ENDDATE DESC, a.INFOPUBLDATE DESC, a.ID DESC
        ) AS rn_audit
    FROM params p
    JOIN lc_auditopinion a
    WHERE a.ENDDATE <= p.as_of_date
      AND MONTH(a.ENDDATE) = 12
      AND DAY(a.ENDDATE) = 31
      AND a.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND a.AUDITREPORTSTYPE = 1
      AND a.OPINIONTYPE IN (1, 2, 3, 4, 5, 7, 11)
),
latest_rating AS (
    SELECT
        r.MAINCODE,
        r.CRCODE,
        r.CRDESC,
        ROW_NUMBER() OVER (
            PARTITION BY r.MAINCODE
            ORDER BY r.INFOPUBLDATE DESC, r.CRDATE DESC, r.ID DESC
        ) AS rn_rating
    FROM params p
    JOIN bond_bdcreditgrading r
    WHERE r.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND r.CRDATE <= p.as_of_date
      AND r.CRTYPECODE = 2
      AND r.CRSYSTEM = 1
      AND r.CRSTATUS = 1
      AND (r.RATINGEFFECTIVEENDDATE IS NULL OR r.RATINGEFFECTIVEENDDATE >= p.as_of_date)
      AND r.CRDESC IS NOT NULL
)
SELECT
    s.MAINCODE,
    s.COMPANYCODE,
    la.audit_group,
    COALESCE(lr.CRCODE, -1) AS rating_code,
    COALESCE(lr.CRDESC, '未评级') AS raw_rating_desc,
    s.VPYIELD AS valuation_yield_pct
FROM sample_bonds s
JOIN latest_audit la
  ON la.COMPANYCODE = s.COMPANYCODE
 AND la.rn_audit = 1
LEFT JOIN latest_rating lr
  ON lr.MAINCODE = s.MAINCODE
 AND lr.rn_rating = 1;
