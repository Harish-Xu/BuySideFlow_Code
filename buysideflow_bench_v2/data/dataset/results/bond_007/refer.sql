-- name: current_valuation_date
WITH valuation_dates AS (
    SELECT DATE(v.ENDDATE) AS valuation_date
    FROM bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_Q047)
    WHERE v.ENDDATE >= DATE_SUB(DATE('{{as_of_date}}'), INTERVAL 7 DAY)
      AND v.ENDDATE < DATE_ADD(DATE('{{as_of_date}}'), INTERVAL 1 DAY)
      AND v.YIELDCODE = 2
      AND v.CREDIBILITYCODE = 1
      AND v.VPYIELD IS NOT NULL
      AND v.TRUEREMAINMATURITY >= 1
      AND v.TRUEREMAINMATURITY <= 5
      AND v.INSERTTIME < DATE_ADD(DATE(v.ENDDATE), INTERVAL 1 DAY)
    GROUP BY DATE(v.ENDDATE)
),
curve_dates AS (
    SELECT DATE(c.ENDDATE) AS valuation_date
    FROM bond_cbyieldcurve c FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE c.ENDDATE >= DATE_SUB(DATE('{{as_of_date}}'), INTERVAL 7 DAY)
      AND c.ENDDATE < DATE_ADD(DATE('{{as_of_date}}'), INTERVAL 1 DAY)
      AND c.CURVECODE = 10
      AND c.YIELDTYPECODE = 1
      AND c.YIELD IS NOT NULL
    GROUP BY DATE(c.ENDDATE)
)
SELECT v.valuation_date
FROM valuation_dates v
JOIN curve_dates c
  ON v.valuation_date = c.valuation_date
ORDER BY v.valuation_date DESC
LIMIT 1;

-- name: current_candidates
WITH city_bonds AS (
    SELECT DISTINCT cn.INNERCODE
    FROM bond_conceptnature cn
    WHERE cn.CONCEPTTYPE = 40
      AND cn.CONNATURE = 4041
      AND DATE(cn.BEGINDATE) <= DATE('{{valuation_date}}')
      AND (cn.ENDDATE IS NULL OR DATE(cn.ENDDATE) > DATE('{{valuation_date}}'))
      AND cn.INSERTTIME < DATE_ADD(DATE('{{valuation_date}}'), INTERVAL 1 DAY)
),
perpetual_bonds AS (
    SELECT DISTINCT pn.INNERCODE
    FROM bond_conceptnature pn
    WHERE pn.CONCEPTTYPE = 40
      AND pn.CONNATURE = 4005
      AND (pn.BEGINDATE IS NULL OR DATE(pn.BEGINDATE) <= DATE('{{valuation_date}}'))
      AND (pn.ENDDATE IS NULL OR DATE(pn.ENDDATE) > DATE('{{valuation_date}}'))
      AND pn.INSERTTIME < DATE_ADD(DATE('{{valuation_date}}'), INTERVAL 1 DAY)
),
base AS (
    SELECT
        bc.MAINCODE,
        bc.INNERCODE,
        bc.SECUCODE AS bond_code,
        bc.SECUABBR AS bond_name,
        DATE(v.ENDDATE) AS valuation_date,
        v.VPYIELD AS bond_ytm,
        v.TRUEREMAINMATURITY AS remaining_years,
        ROW_NUMBER() OVER (
            PARTITION BY bc.MAINCODE
            ORDER BY COALESCE(bc.SECUCODE, ''), bc.INNERCODE, v.ID DESC
        ) AS rn
    FROM bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_Q047)
    JOIN bond_code bc
      ON v.INNERCODE = bc.INNERCODE
    JOIN bond_basicinfo bi
      ON bc.INNERCODE = bi.INNERCODE
    JOIN city_bonds cbn
      ON bc.INNERCODE = cbn.INNERCODE
    LEFT JOIN perpetual_bonds pb
      ON bc.INNERCODE = pb.INNERCODE
    WHERE v.ENDDATE >= DATE('{{valuation_date}}')
      AND v.ENDDATE < DATE_ADD(DATE('{{valuation_date}}'), INTERVAL 1 DAY)
      AND v.YIELDCODE = 2
      AND v.CREDIBILITYCODE = 1
      AND v.VPYIELD IS NOT NULL
      AND v.TRUEREMAINMATURITY >= 1
      AND v.TRUEREMAINMATURITY <= 5
      AND v.INSERTTIME < DATE_ADD(DATE(v.ENDDATE), INTERVAL 1 DAY)
      AND bc.MAINCODE IS NOT NULL
      AND pb.INNERCODE IS NULL
      AND (bc.LISTEDDATE IS NULL OR DATE(bc.LISTEDDATE) <= DATE(v.ENDDATE))
      AND (bc.INTERESTENDDATE IS NULL OR DATE(bc.INTERESTENDDATE) >= DATE(v.ENDDATE))
      AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) >= DATE(v.ENDDATE))
      AND (
          bc.BONDTYPELEVEL1 IN (1600,1700,1800,2500,2600,2800,2900,3100,3200,3300,3700)
          OR bc.BONDTYPELEVEL2 IN (1901,1903)
      )
      AND COALESCE(bc.BONDNATURE, 0) NOT IN (3, 21, 27)
      AND CONCAT_WS(' ', bc.SECUABBR, bc.CHINAME, bc.BONDTYPELEVEL1DESC,
                    bc.BONDTYPELEVEL2DESC, bi.BONDFULLNAME) NOT REGEXP '次级|二级资本'
      AND COALESCE(bi.RATEIFFLOAT, 2) <> 1
      AND (bi.OPMATURITY IS NULL OR TRIM(bi.OPMATURITY) = '')
      AND (
          bi.OPTIONTYPE IS NULL
          OR (
              bi.OPTIONTYPE NOT LIKE '%赎回%'
              AND bi.OPTIONTYPE NOT LIKE '%回售%'
              AND bi.OPTIONTYPE NOT LIKE '%调整票面利率%'
              AND bi.OPTIONTYPE NOT LIKE '%续期%'
          )
      )
),
latest_rating AS (
    SELECT MAINCODE, CRDESC
    FROM (
        SELECT
            r.MAINCODE,
            r.CRDESC,
            ROW_NUMBER() OVER (
                PARTITION BY r.MAINCODE
                ORDER BY r.INFOPUBLDATE DESC, r.CRDATE DESC, r.ID DESC
            ) AS rn
        FROM bond_bdcreditgrading r FORCE INDEX (IX_BOND_BDCREDITGRADING_Q047)
        JOIN (SELECT DISTINCT MAINCODE FROM base) bm
          ON r.MAINCODE = bm.MAINCODE
        WHERE r.INFOPUBLDATE < DATE_ADD(DATE('{{valuation_date}}'), INTERVAL 1 DAY)
          AND r.CRTYPECODE = 2
          AND r.CRSYSTEM = 1
          AND r.CRDESC IS NOT NULL
    ) ranked
    WHERE rn = 1
)
SELECT
    b.MAINCODE,
    b.INNERCODE,
    b.bond_code,
    b.bond_name,
    b.valuation_date,
    b.remaining_years,
    b.bond_ytm
FROM base b
JOIN latest_rating r
  ON b.MAINCODE = r.MAINCODE
WHERE b.rn = 1
  AND r.CRDESC = 'AAA';

-- name: history_valuations
SELECT
    bc.MAINCODE,
    bc.INNERCODE,
    bc.SECUCODE AS bond_code,
    DATE(v.ENDDATE) AS valuation_date,
    v.VPYIELD AS bond_ytm,
    v.TRUEREMAINMATURITY AS remaining_years,
    v.ID
FROM bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL)
JOIN bond_code bc
  ON v.INNERCODE = bc.INNERCODE
WHERE v.INNERCODE IN ({{innercodes}})
  AND v.ENDDATE >= DATE('{{start_date}}')
  AND v.ENDDATE < DATE_ADD(DATE('{{end_date}}'), INTERVAL 1 DAY)
  AND v.YIELDCODE = 2
  AND v.CREDIBILITYCODE = 1
  AND v.VPYIELD IS NOT NULL
  AND v.TRUEREMAINMATURITY IS NOT NULL
  AND v.INSERTTIME < DATE_ADD(DATE(v.ENDDATE), INTERVAL 1 DAY)
  AND (bc.LISTEDDATE IS NULL OR DATE(bc.LISTEDDATE) <= DATE(v.ENDDATE))
  AND (bc.INTERESTENDDATE IS NULL OR DATE(bc.INTERESTENDDATE) >= DATE(v.ENDDATE))
  AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) >= DATE(v.ENDDATE));

-- name: treasury_curve
WITH ranked_curve AS (
    SELECT
        DATE(c.ENDDATE) AS valuation_date,
        c.YEARSTOMATURITY AS maturity_year,
        c.YIELD AS yield_value,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(c.ENDDATE), c.YEARSTOMATURITY
            ORDER BY c.UPDATETIME DESC, c.ID DESC
        ) AS rn
    FROM bond_cbyieldcurve c FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE c.CURVECODE = 10
      AND c.YIELDTYPECODE = 1
      AND c.ENDDATE >= DATE('{{start_date}}')
      AND c.ENDDATE < DATE_ADD(DATE('{{end_date}}'), INTERVAL 1 DAY)
      AND c.YIELD IS NOT NULL
)
SELECT
    valuation_date,
    maturity_year,
    yield_value
FROM ranked_curve
WHERE rn = 1
ORDER BY valuation_date, maturity_year;
