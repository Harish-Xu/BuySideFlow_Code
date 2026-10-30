WITH params AS (
    SELECT
        DATE('2024-12-31') AS as_of_date,
        DATE('2024-01-01') AS year_start,
        DATE('2025-01-01') AS year_end
),
enterprise_credit_bonds AS (
    SELECT DISTINCT
        bc.COMPANYCODE,
        bc.MAINCODE,
        bc.ISSUER
    FROM bond_code bc
    WHERE bc.COMPANYCODE IS NOT NULL
      AND bc.MAINCODE IS NOT NULL
      AND (
            bc.BONDTYPELEVEL1 IN (1600,1700,1800,2500,2600,2800,2900,3100,3200,3300,3700)
         OR bc.BONDTYPELEVEL2 IN (1901,1903)
      )
),
bond_start_dates AS (
    SELECT
        bc.MAINCODE,
        MIN(
            LEAST(
                COALESCE(bc.LISTEDDATE, '9999-12-31'),
                COALESCE(bi.LISTEDDATE, '9999-12-31'),
                COALESCE(bi.VALUEDATE, '9999-12-31'),
                COALESCE(bi.ISSUEDATE, '9999-12-31'),
                COALESCE(bi.INITIALINFOPUBLDATE, '9999-12-31')
            )
        ) AS start_date
    FROM bond_code bc
    LEFT JOIN bond_basicinfo bi
      ON bi.INNERCODE = bc.INNERCODE
    WHERE bc.MAINCODE IS NOT NULL
    GROUP BY bc.MAINCODE
),
city_issuers AS (
    SELECT DISTINCT
        bc.COMPANYCODE
    FROM params p
    JOIN bond_code bc
    JOIN bond_conceptnature cn
      ON cn.INNERCODE = bc.INNERCODE
    WHERE bc.COMPANYCODE IS NOT NULL
      AND cn.CONCEPTTYPE = 40
      AND cn.CONNATURE = 4041
      AND (cn.BEGINDATE IS NULL OR cn.BEGINDATE <= p.as_of_date)
      AND (cn.ENDDATE IS NULL OR cn.ENDDATE > p.as_of_date)
      AND (cn.INSERTTIME IS NULL OR cn.INSERTTIME < p.year_end)
),
live_credit_bonds AS (
    SELECT DISTINCT
        bc.COMPANYCODE,
        bc.MAINCODE,
        bc.ISSUER
    FROM params p
    JOIN bond_code bc
    JOIN bond_start_dates bsd
      ON bsd.MAINCODE = bc.MAINCODE
    JOIN city_issuers ci
      ON ci.COMPANYCODE = bc.COMPANYCODE
    WHERE bc.COMPANYCODE IS NOT NULL
      AND bc.MAINCODE IS NOT NULL
      AND bsd.start_date <= p.as_of_date
      AND (bc.INTERESTENDDATE IS NULL OR bc.INTERESTENDDATE >= p.as_of_date)
      AND (bc.DELISTDATE IS NULL OR bc.DELISTDATE > p.as_of_date)
      AND (
            bc.BONDTYPELEVEL1 IN (1600,1700,1800,2500,2600,2800,2900,3100,3200,3300,3700)
         OR bc.BONDTYPELEVEL2 IN (1901,1903)
      )
),
issuer_name_counts AS (
    SELECT
        COMPANYCODE,
        ISSUER,
        COUNT(*) AS name_count
    FROM live_credit_bonds
    WHERE ISSUER IS NOT NULL
    GROUP BY COMPANYCODE, ISSUER
),
issuer_name AS (
    SELECT
        COMPANYCODE,
        ISSUER,
        ROW_NUMBER() OVER (
            PARTITION BY COMPANYCODE
            ORDER BY name_count DESC, ISSUER ASC
        ) AS rn
    FROM issuer_name_counts
),
downgrade_events AS (
    SELECT DISTINCT
        ecb.COMPANYCODE,
        g.MAINCODE,
        DATE(g.INFOPUBLDATE) AS announcement_day
    FROM params p
    JOIN bond_bdcreditgrading g
    JOIN enterprise_credit_bonds ecb
      ON ecb.MAINCODE = g.MAINCODE
    JOIN city_issuers ci
      ON ci.COMPANYCODE = ecb.COMPANYCODE
    WHERE g.INFOPUBLDATE >= p.year_start
      AND g.INFOPUBLDATE < p.year_end
      AND g.CRTYPECODE = 2
      AND g.CRSYSTEM = 1
      AND g.CRSTATUS = 1
      AND g.CRCHANGE = '调低'
),
downgrade_by_issuer AS (
    SELECT
        COMPANYCODE,
        COUNT(*) AS downgrade_event_count
    FROM downgrade_events
    GROUP BY COMPANYCODE
),
candidate_issuers AS (
    SELECT
        lcb.COMPANYCODE,
        COUNT(DISTINCT lcb.MAINCODE) AS live_bond_count,
        MAX(dbi.downgrade_event_count) AS downgrade_event_count
    FROM live_credit_bonds lcb
    JOIN downgrade_by_issuer dbi
      ON dbi.COMPANYCODE = lcb.COMPANYCODE
    GROUP BY lcb.COMPANYCODE
    HAVING COUNT(DISTINCT lcb.MAINCODE) >= 5
),
valuation_ranked AS (
    SELECT
        lcb.COMPANYCODE,
        lcb.MAINCODE,
        v.VPYIELD AS vpyield,
        ROW_NUMBER() OVER (
            PARTITION BY lcb.MAINCODE
            ORDER BY v.UPDATETIME DESC, v.INSERTTIME DESC, v.ID DESC, bc.INNERCODE ASC
        ) AS rn
    FROM params p
    JOIN live_credit_bonds lcb
    JOIN bond_code bc
      ON bc.MAINCODE = lcb.MAINCODE
    JOIN bond_cbvaluationall v
      ON v.INNERCODE = bc.INNERCODE
    WHERE v.ENDDATE = p.as_of_date
      AND v.CREDIBILITYCODE = 1
      AND v.YIELDCODE = 2
      AND v.VPYIELD IS NOT NULL
      AND (v.INSERTTIME IS NULL OR v.INSERTTIME < p.year_end)
)
SELECT
    iname.ISSUER AS `发行人`,
    ci.live_bond_count AS `存续债数量`,
    AVG(CASE WHEN vr.rn = 1 THEN vr.vpyield END) AS `平均估值收益率`,
    ci.downgrade_event_count AS `下调事件次数`
FROM candidate_issuers ci
JOIN issuer_name iname
  ON iname.COMPANYCODE = ci.COMPANYCODE
 AND iname.rn = 1
JOIN live_credit_bonds lcb
  ON lcb.COMPANYCODE = ci.COMPANYCODE
LEFT JOIN valuation_ranked vr
  ON vr.MAINCODE = lcb.MAINCODE
 AND vr.rn = 1
GROUP BY
    iname.ISSUER,
    ci.live_bond_count,
    ci.downgrade_event_count
ORDER BY
    ci.downgrade_event_count DESC,
    `平均估值收益率` DESC,
    iname.ISSUER ASC
LIMIT 10;
