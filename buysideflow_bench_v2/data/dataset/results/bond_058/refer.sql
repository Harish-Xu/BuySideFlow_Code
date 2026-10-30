WITH valuation_date AS (
    SELECT DATE(ENDDATE) AS latest_enddate
    FROM bond_cbvaluationall FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
    WHERE ENDDATE < DATE_ADD(DATE('2024-12-31'), INTERVAL 1 DAY)
      AND CREDIBILITYCODE = 1
      AND VPYIELD IS NOT NULL
      AND TRUEREMAINMATURITY IS NOT NULL
      AND (INSERTTIME IS NULL OR INSERTTIME < DATE_ADD(DATE('2024-12-31'), INTERVAL 1 DAY))
    ORDER BY ENDDATE DESC
    LIMIT 1
),
recent_trade_days AS (
    SELECT trade_date
    FROM (
        SELECT DATE(TRADINGDATE) AS trade_date
        FROM qt_tradingdaynew
        WHERE IFTRADINGDAY = 1
          AND SECUMARKET = 89
          AND TRADINGDATE <= DATE('2024-12-31')
        ORDER BY TRADINGDATE DESC
        LIMIT 20
    ) t
),
latest_rating AS (
    SELECT MAINCODE, CRDESC
    FROM (
        SELECT
            r.MAINCODE,
            r.CRDESC,
            r.CRSTATUS,
            r.RATINGEFFECTIVEENDDATE,
            ROW_NUMBER() OVER (
                PARTITION BY r.MAINCODE
                ORDER BY r.INFOPUBLDATE DESC, r.CRDATE DESC, r.ID DESC
            ) AS rn
        FROM bond_bdcreditgrading r
        WHERE r.INFOPUBLDATE < DATE('2025-01-01')
          AND r.CRTYPECODE = 2
          AND r.CRSYSTEM = 1
    ) r
    WHERE rn = 1
      AND CRSTATUS = 1
      AND CRDESC IS NOT NULL
      AND (RATINGEFFECTIVEENDDATE IS NULL OR RATINGEFFECTIVEENDDATE >= DATE('2024-12-31'))
),
latest_industry AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME
    FROM (
        SELECT
            i.COMPANYCODE,
            i.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY i.COMPANYCODE
                ORDER BY i.EFFECTIVEDATE DESC, i.INFOPUBLDATE DESC, i.ID DESC
            ) AS rn
        FROM bond_biindustry i
        WHERE i.STANDARD = 38
          AND i.EFFECTIVEDATE <= DATE('2024-12-31')
          AND i.INFOPUBLDATE < DATE('2025-01-01')
          AND (i.CANCELDATE IS NULL OR i.CANCELDATE > DATE('2024-12-31'))
    ) i
    WHERE rn = 1
),
eligible_codes AS (
    -- Materialize the filtered code set before valuation lookups; keep this join order.
    SELECT DISTINCT STRAIGHT_JOIN bc.MAINCODE,bc.INNERCODE,bc.SECUMARKET,bc.SECUCODE,bc.SECUABBR,
           bc.ISSUER,bc.COMPANYCODE
    FROM bond_code bc
    JOIN bond_basicinfo bi ON bi.MAINCODE = bc.MAINCODE
    JOIN latest_industry i ON i.COMPANYCODE = bc.COMPANYCODE AND i.FIRSTINDUSTRYNAME = '交通运输'
    JOIN latest_rating r ON r.MAINCODE = bc.MAINCODE AND r.CRDESC = 'AAA'
    WHERE
        (bc.BONDTYPELEVEL1 = 1800 OR bc.BONDTYPELEVEL2 = 1800)
          AND bc.MAINCODE IS NOT NULL
          AND bc.SECUCODE IS NOT NULL
          AND bc.SECUABBR IS NOT NULL
          AND bc.ISSUER IS NOT NULL
          AND (bc.LISTEDDATE IS NULL OR DATE(bc.LISTEDDATE) <= DATE('2024-12-31'))
          AND DATE(bc.INTERESTENDDATE) > DATE('2024-12-31')
          AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) > DATE('2024-12-31'))
          AND COALESCE(bc.SECUABBR, '') NOT LIKE '%永续%'
          AND COALESCE(bc.CHINAME, '') NOT LIKE '%永续%'
          AND COALESCE(bi.BONDFULLNAME, '') NOT LIKE '%永续%'
          AND COALESCE(bc.SECUABBR, '') NOT LIKE '%次级%'
          AND COALESCE(bc.CHINAME, '') NOT LIKE '%次级%'
          AND COALESCE(bi.BONDFULLNAME, '') NOT LIKE '%次级%'
          AND COALESCE(bc.BONDTYPELEVEL1DESC, '') NOT LIKE '%次级%'
          AND COALESCE(bc.BONDTYPELEVEL2DESC, '') NOT LIKE '%次级%'
          AND COALESCE(bi.BONDNATURE, -1) NOT IN (3, 21)
          AND bc.MAINCODE NOT IN (
              SELECT DISTINCT cb.MAINCODE
              FROM bond_conceptnature cn
              JOIN bond_code cb ON cb.INNERCODE = cn.INNERCODE
              WHERE cb.MAINCODE IS NOT NULL
                AND cn.CONCEPTTYPE = 40 AND cn.CONNATURE = 4005
                AND (cn.BEGINDATE IS NULL OR cn.BEGINDATE <= DATE('2024-12-31'))
                AND (cn.ENDDATE IS NULL OR cn.ENDDATE > DATE('2024-12-31'))
                AND cn.INSERTTIME < DATE('2025-01-01')
          )
          AND bc.MAINCODE NOT IN (
              SELECT DISTINCT dc.MAINCODE
              FROM bond_default def
              JOIN bond_code dc ON dc.INNERCODE = def.INNERCODE
              WHERE dc.MAINCODE IS NOT NULL
                AND def.INFOPUBLDATE < DATE('2025-01-01')
                AND (def.DEFAULTDATE IS NULL OR def.DEFAULTDATE < DATE('2025-01-01'))
                -- Actual interest/principal/other default; exclude forecasts and warnings.
                AND def.DEFAULTPROG IN (201, 202, 203, 801)
          )
),
candidate AS (
    SELECT MAINCODE,INNERCODE,SECUMARKET,SECUCODE,SECUABBR,ISSUER,COMPANYCODE,VPYIELD
    FROM (
        SELECT bc.*,v.VPYIELD,
               ROW_NUMBER() OVER (
                   PARTITION BY bc.MAINCODE
                   ORDER BY CASE WHEN bc.SECUMARKET = 89 THEN 0 ELSE 1 END,
                            bc.SECUCODE,bc.INNERCODE,v.ID DESC
               ) AS rn
        FROM eligible_codes bc
        CROSS JOIN valuation_date d
        JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL)
          ON v.INNERCODE = bc.INNERCODE
         AND v.ENDDATE >= d.latest_enddate
         AND v.ENDDATE < DATE_ADD(d.latest_enddate, INTERVAL 1 DAY)
        WHERE v.CREDIBILITYCODE = 1
          AND v.YIELDCODE = 2
          AND v.VPYIELD IS NOT NULL
          AND v.TRUEREMAINMATURITY > 3
          AND v.TRUEREMAINMATURITY <= 5
          AND (v.INSERTTIME IS NULL OR v.INSERTTIME < DATE('2025-01-01'))
    ) x
    WHERE rn = 1
)
SELECT STRAIGHT_JOIN
    c.SECUCODE AS `债券代码`,
    c.SECUABBR AS `债券简称`,
    c.ISSUER AS `发行人`,
    ROUND(c.VPYIELD, 2) AS `估值收益率`,
    COUNT(DISTINCT DATE(q.TRADINGDAY)) AS `成交天数`,
    ROUND(SUM(CASE WHEN q.TURNOVERVALUE > 0 THEN q.TURNOVERVALUE ELSE 0 END) / 100000000, 2) AS `成交金额`
FROM candidate c
JOIN bond_code bc ON bc.MAINCODE = c.MAINCODE
JOIN recent_trade_days d
JOIN bond_basicquote q FORCE INDEX (IX_BOND_BASICQUOTE)
  ON q.INNERCODE = bc.INNERCODE
 AND q.SECUMARKET = bc.SECUMARKET
 AND q.TRADINGDAY = d.trade_date
WHERE (bc.LISTEDDATE IS NULL OR DATE(bc.LISTEDDATE) <= DATE('2024-12-31'))
  AND DATE(bc.INTERESTENDDATE) > DATE('2024-12-31')
  AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) > DATE('2024-12-31'))
  AND (COALESCE(q.TURNOVERVALUE, 0) > 0 OR COALESCE(q.TURNOVERVOLUME, 0) > 0)
  AND q.INSERTTIME < DATE('2025-01-01')
GROUP BY c.MAINCODE,c.SECUCODE,c.SECUABBR,c.ISSUER,c.VPYIELD
ORDER BY `成交天数` DESC,
         SUM(CASE WHEN q.TURNOVERVALUE > 0 THEN q.TURNOVERVALUE ELSE 0 END) DESC,
         c.SECUCODE ASC
LIMIT 5;
