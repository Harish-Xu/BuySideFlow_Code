WITH params AS (
    SELECT DATE('2024-12-31') AS as_of_date
),
subject_downgrade_ranked AS (
    SELECT
        r.COMPANYCODE,
        DATE(r.INFOPUBLDATE) AS downgrade_date,
        r.LASTCRDESC AS pre_rating,
        r.CRDESC AS post_rating,
        ROW_NUMBER() OVER (
            PARTITION BY r.COMPANYCODE
            ORDER BY r.INFOPUBLDATE DESC, r.CRDATE DESC, r.UPDATETIME DESC, r.ID DESC
        ) AS rn
    FROM bond_comcreditgrading r
    JOIN params p
      ON 1 = 1
    WHERE r.INFOPUBLDATE >= DATE('2024-01-01')
      AND r.INFOPUBLDATE < DATE('2025-01-01')
      AND r.CRSYSTEM = 1
      AND r.CRTYPE = 3
      AND r.CRWISH = 2
      AND r.CRSTATUS = 1
      AND r.CRCHANGE = '调低'
      AND r.COMPANYCODE IS NOT NULL
      AND r.LASTCRDESC IS NOT NULL
      AND r.CRDESC IS NOT NULL
),
subject_downgrade AS (
    SELECT COMPANYCODE, downgrade_date, pre_rating, post_rating
    FROM subject_downgrade_ranked
    WHERE rn = 1
),
latest_subject_rating_ranked AS (
    SELECT
        r.COMPANYCODE,
        r.CRDESC AS year_end_rating,
        ROW_NUMBER() OVER (
            PARTITION BY r.COMPANYCODE
            ORDER BY r.INFOPUBLDATE DESC, r.CRDATE DESC, r.UPDATETIME DESC, r.ID DESC
        ) AS rn
    FROM bond_comcreditgrading r
    JOIN params p
      ON 1 = 1
    WHERE r.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND r.CRSYSTEM = 1
      AND r.CRTYPE = 3
      AND r.CRWISH = 2
      AND r.CRSTATUS = 1
      AND r.CRDESC IS NOT NULL
      AND (
          r.RATINGEFFECTIVEENDDATE IS NULL
          OR r.RATINGEFFECTIVEENDDATE >= p.as_of_date
      )
),
latest_subject_rating AS (
    SELECT COMPANYCODE, year_end_rating
    FROM latest_subject_rating_ranked
    WHERE rn = 1
),
valuation_ranked AS (
    SELECT
        bc.MAINCODE,
        bc.COMPANYCODE,
        bc.SECUCODE,
        bc.SECUABBR,
        v.VPYIELD,
        v.TRUEREMAINMATURITY,
        ROW_NUMBER() OVER (
            PARTITION BY bc.MAINCODE
            ORDER BY
                v.UPDATETIME DESC,
                v.ID DESC,
                CASE WHEN bc.SECUMARKET = 89 THEN 0 ELSE 1 END,
                bc.SECUCODE ASC,
                bc.INNERCODE ASC
        ) AS rn
    FROM params p
    JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
      ON v.ENDDATE >= p.as_of_date
     AND v.ENDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    JOIN bond_code bc
      ON bc.INNERCODE = v.INNERCODE
    WHERE v.CREDIBILITYCODE = 1
      AND v.VPYIELD IS NOT NULL
      AND v.TRUEREMAINMATURITY > 0
      AND v.TRUEREMAINMATURITY <= 10
      AND bc.MAINCODE IS NOT NULL
      AND bc.COMPANYCODE IS NOT NULL
      AND bc.SECUCODE IS NOT NULL
      AND COALESCE(bc.BONDNATURE, -1) <> 30
      AND (v.INSERTTIME IS NULL OR v.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
      AND (bc.LISTEDDATE IS NULL OR bc.LISTEDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
      AND (bc.INTERESTENDDATE IS NULL OR bc.INTERESTENDDATE >= p.as_of_date)
      AND (bc.DELISTDATE IS NULL OR bc.DELISTDATE >= p.as_of_date)
),
bond_pool AS (
    SELECT
        v.MAINCODE,
        v.COMPANYCODE,
        v.SECUCODE,
        v.SECUABBR,
        v.VPYIELD,
        r.year_end_rating,
        CASE
            WHEN v.TRUEREMAINMATURITY <= 1 THEN '(0,1]'
            WHEN v.TRUEREMAINMATURITY <= 3 THEN '(1,3]'
            WHEN v.TRUEREMAINMATURITY <= 5 THEN '(3,5]'
            ELSE '(5,10]'
        END AS maturity_bucket
    FROM valuation_ranked v
    JOIN latest_subject_rating r
      ON r.COMPANYCODE = v.COMPANYCODE
    WHERE v.rn = 1
),
peer_ranked AS (
    SELECT
        year_end_rating,
        maturity_bucket,
        VPYIELD,
        ROW_NUMBER() OVER (
            PARTITION BY year_end_rating, maturity_bucket
            ORDER BY VPYIELD, MAINCODE
        ) AS rn,
        COUNT(*) OVER (
            PARTITION BY year_end_rating, maturity_bucket
        ) AS cnt
    FROM bond_pool
),
peer_median AS (
    SELECT
        year_end_rating,
        maturity_bucket,
        AVG(VPYIELD) AS median_yield
    FROM peer_ranked
    WHERE rn IN ((cnt + 1) DIV 2, (cnt + 2) DIV 2)
    GROUP BY year_end_rating, maturity_bucket
),
qualified AS (
    SELECT
        b.SECUCODE,
        b.SECUABBR,
        d.downgrade_date,
        d.pre_rating,
        d.post_rating,
        b.year_end_rating,
        b.maturity_bucket,
        b.VPYIELD,
        m.median_yield,
        (b.VPYIELD - m.median_yield) * 100 AS spread_bp
    FROM subject_downgrade d
    JOIN bond_pool b
      ON b.COMPANYCODE = d.COMPANYCODE
    JOIN peer_median m
      ON m.year_end_rating = b.year_end_rating
     AND m.maturity_bucket = b.maturity_bucket
    WHERE b.VPYIELD - m.median_yield <= 0.10
)
SELECT
    SECUCODE AS `债券代码`,
    SECUABBR AS `债券简称`,
    downgrade_date AS `降级公告日`,
    pre_rating AS `降级前评级`,
    post_rating AS `降级后评级`,
    year_end_rating AS `年末主体评级`,
    maturity_bucket AS `期限分组`,
    ROUND(VPYIELD, 4) AS `年末估值收益率`,
    ROUND(median_yield, 4) AS `同组中位数收益率`,
    ROUND(spread_bp, 2) AS `相对中位数利差`
FROM qualified
ORDER BY spread_bp ASC, SECUCODE ASC;
