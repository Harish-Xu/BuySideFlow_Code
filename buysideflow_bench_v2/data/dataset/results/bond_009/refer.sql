-- name: current_valuation_date
WITH valuation_dates AS (
    SELECT DATE(v.ENDDATE) AS valuation_date
    FROM bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
    WHERE v.ENDDATE >= DATE_SUB(DATE('{{as_of_date}}'), INTERVAL 14 DAY)
      AND v.ENDDATE < DATE_ADD(DATE('{{as_of_date}}'), INTERVAL 1 DAY)
      AND v.CREDIBILITYCODE = 1
      AND v.YIELDCODE = 2
      AND COALESCE(v.SPECIALMARK, 0) <> 1
      AND v.VPYIELD IS NOT NULL
      AND v.TRUEREMAINMATURITY >= 2
      AND v.TRUEREMAINMATURITY <= 5
      AND v.INSERTTIME < DATE_ADD(DATE(v.ENDDATE), INTERVAL 1 DAY)
    GROUP BY DATE(v.ENDDATE)
),
curve_dates AS (
    SELECT DATE(c.ENDDATE) AS valuation_date
    FROM bond_cbyieldcurve c FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE c.ENDDATE >= DATE_SUB(DATE('{{as_of_date}}'), INTERVAL 14 DAY)
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
WITH params AS (
    SELECT DATE('{{valuation_date}}') AS valuation_date
),
valuation_ranked AS (
    SELECT
        bc.MAINCODE,
        bc.INNERCODE,
        bc.COMPANYCODE,
        bc.SECUCODE AS bond_code,
        bc.SECUABBR AS bond_name,
        bc.ISSUER AS issuer,
        bc.BONDNATURE,
        bc.BONDTYPELEVEL1,
        bc.BONDTYPELEVEL1DESC,
        bc.BONDTYPELEVEL2,
        bc.BONDTYPELEVEL2DESC,
        v.VPYIELD AS valuation_yield_pct,
        v.TRUEREMAINMATURITY AS remaining_years,
        v.SPECIALMARK,
        ROW_NUMBER() OVER (
            PARTITION BY bc.MAINCODE
            ORDER BY
                CASE WHEN bc.SECUMARKET = 89 THEN 0 ELSE 1 END,
                COALESCE(bc.SECUCODE, ''),
                bc.INNERCODE,
                v.UPDATETIME DESC, v.ID DESC
        ) AS rn
    FROM params p
    JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
      ON v.ENDDATE >= p.valuation_date
     AND v.ENDDATE < DATE_ADD(p.valuation_date, INTERVAL 1 DAY)
    JOIN bond_code bc
      ON v.INNERCODE = bc.INNERCODE
    WHERE v.CREDIBILITYCODE = 1
      AND v.YIELDCODE = 2
      AND v.INSERTTIME < DATE_ADD(p.valuation_date, INTERVAL 1 DAY)
      AND bc.MAINCODE IS NOT NULL
      AND bc.INNERCODE IS NOT NULL
      AND bc.ISSUER IS NOT NULL
      AND (bc.LISTEDDATE IS NULL OR DATE(bc.LISTEDDATE) <= p.valuation_date)
      AND (bc.INTERESTENDDATE IS NULL OR DATE(bc.INTERESTENDDATE) >= p.valuation_date)
      AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) >= p.valuation_date)
      AND (
          bc.BONDTYPELEVEL1 IN (1600, 1700, 1800, 2500, 2600, 2800, 2900, 3100, 3200, 3300, 3700)
          OR bc.BONDTYPELEVEL2 IN (1901, 1903)
      )
),
latest_rating AS (
    SELECT MAINCODE, rating_desc
    FROM (
        SELECT
            g.MAINCODE,
            g.CRDESC AS rating_desc,
            ROW_NUMBER() OVER (
                PARTITION BY g.MAINCODE
                ORDER BY g.INFOPUBLDATE DESC, g.CRDATE DESC, g.ID DESC
            ) AS rn
        FROM params p
        JOIN bond_bdcreditgrading g FORCE INDEX (IX_BOND_BDCREDITGRADING_Q047)
          ON g.INFOPUBLDATE < DATE_ADD(p.valuation_date, INTERVAL 1 DAY)
        JOIN (SELECT DISTINCT MAINCODE FROM valuation_ranked) bm
          ON g.MAINCODE = bm.MAINCODE
        WHERE g.CRTYPECODE = 2
          AND g.CRSYSTEM = 1
          AND g.CRSTATUS = 1
          AND (g.CRDATE IS NULL OR g.CRDATE < DATE_ADD(p.valuation_date, INTERVAL 1 DAY))
          AND (g.RATINGEFFECTIVEENDDATE IS NULL OR g.RATINGEFFECTIVEENDDATE >= p.valuation_date)
    ) ranked
    WHERE rn = 1
      AND rating_desc = 'AAA'
)
SELECT
    p.valuation_date,
    CAST(v.MAINCODE AS UNSIGNED) AS maincode,
    CAST(v.INNERCODE AS UNSIGNED) AS innercode,
    v.bond_code,
    v.bond_name,
    v.issuer,
    r.rating_desc,
    v.remaining_years,
    v.valuation_yield_pct
FROM params p
JOIN valuation_ranked v
  ON v.rn = 1
JOIN latest_rating r
  ON v.MAINCODE = r.MAINCODE
WHERE COALESCE(v.SPECIALMARK,0)<>1 AND v.valuation_yield_pct IS NOT NULL
  AND v.remaining_years BETWEEN 2 AND 5;

-- name: recent_trade_days
SELECT DATE(TRADINGDATE) AS trade_date
FROM qt_tradingdaynew
WHERE IFTRADINGDAY = 1
  AND SECUMARKET IN (18, 83, 89, 90)
  AND TRADINGDATE <= DATE('{{valuation_date}}')
GROUP BY DATE(TRADINGDATE)
HAVING COUNT(DISTINCT SECUMARKET) = 4
ORDER BY trade_date DESC
LIMIT 20;

-- name: trade_activity
SELECT
    CAST(bc.MAINCODE AS UNSIGNED) AS maincode,
    COUNT(DISTINCT DATE(q.TRADINGDAY)) AS trade_days_20
FROM bond_basicquote q FORCE INDEX (IX_BOND_BASICQUOTE)
JOIN bond_code bc
  ON q.INNERCODE = bc.INNERCODE
WHERE bc.MAINCODE IN ({{maincodes}})
  AND q.TRADINGDAY IN ({{trade_dates}})
  AND q.INSERTTIME < DATE_ADD(DATE('{{as_of_date}}'), INTERVAL 1 DAY)
  AND (
      q.IFTRADING = 1
      OR COALESCE(q.TURNOVERVALUE, 0) > 0
      OR COALESCE(q.TURNOVERVOLUME, 0) > 0
  )
GROUP BY bc.MAINCODE;

-- name: treasury_curve
WITH ranked_curve AS (
    SELECT
        c.YEARSTOMATURITY AS maturity_year,
        c.YIELD AS yield_value,
        ROW_NUMBER() OVER (
            PARTITION BY c.YEARSTOMATURITY
            ORDER BY c.UPDATETIME DESC, c.ID DESC
        ) AS rn
    FROM bond_cbyieldcurve c FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE c.ENDDATE >= DATE('{{valuation_date}}')
      AND c.ENDDATE < DATE_ADD(DATE('{{valuation_date}}'), INTERVAL 1 DAY)
      AND c.CURVECODE = 10
      AND c.YIELDTYPECODE = 1
      AND c.YIELD IS NOT NULL
)
SELECT
    maturity_year,
    yield_value
FROM ranked_curve
WHERE rn = 1
ORDER BY maturity_year;
