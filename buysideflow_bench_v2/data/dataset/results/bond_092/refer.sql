WITH params AS (
    SELECT
        DATE('2025-06-30') AS as_of_date,
        TIMESTAMP('2025-07-01 00:00:00') AS next_day_ts
),
active_codes AS (
    SELECT
        bc.MAINCODE,
        bc.INNERCODE,
        bc.SECUCODE AS bond_code,
        bc.SECUABBR AS bond_name,
        bc.CHINAME AS bond_full_name,
        bc.ISSUER AS issuer,
        bc.SECUMARKET,
        bc.BONDTYPELEVEL1,
        bc.BONDTYPELEVEL1DESC,
        bc.BONDTYPELEVEL2,
        bc.BONDTYPELEVEL2DESC
    FROM bond_code bc
    CROSS JOIN params p
    WHERE bc.MAINCODE IS NOT NULL
      AND bc.INNERCODE IS NOT NULL
      AND bc.SECUCODE IS NOT NULL
      AND bc.BONDTYPELEVEL1 = 1800
      AND COALESCE(bc.BONDTYPELEVEL1DESC, '') NOT LIKE '%次级%'
      AND COALESCE(bc.BONDTYPELEVEL2DESC, '') NOT LIKE '%次级%'
      AND COALESCE(bc.SECUABBR, '') NOT LIKE '%次级%'
      AND COALESCE(bc.CHINAME, '') NOT LIKE '%次级%'
      AND COALESCE(bc.SECUABBR, '') NOT LIKE '%永续%'
      AND COALESCE(bc.CHINAME, '') NOT LIKE '%永续%'
      AND COALESCE(bc.SECUABBR, '') NOT LIKE '%可续%'
      AND COALESCE(bc.CHINAME, '') NOT LIKE '%可续%'
      AND (bc.LISTEDDATE IS NULL OR bc.LISTEDDATE <= p.as_of_date)
      AND (bc.INTERESTENDDATE IS NULL OR bc.INTERESTENDDATE >= p.as_of_date)
      AND (bc.DELISTDATE IS NULL OR bc.DELISTDATE >= p.as_of_date)
),
active_maincodes AS (
    SELECT DISTINCT MAINCODE
    FROM active_codes
),
basic_ranked AS (
    SELECT
        bi.MAINCODE,
        bi.OPTIONTYPE,
        bi.RATEADJUSTMODE,
        bi.OPMATURITY,
        ROW_NUMBER() OVER (
            PARTITION BY bi.MAINCODE
            ORDER BY bi.UPDATETIME DESC, bi.ID DESC
        ) AS rn
    FROM active_maincodes am
    JOIN bond_basicinfo bi
      ON bi.MAINCODE = am.MAINCODE
    CROSS JOIN params p
    WHERE bi.INITIALINFOPUBLDATE < p.next_day_ts
),
clean_maincodes AS (
    SELECT DISTINCT
        ac.MAINCODE
    FROM active_codes ac
    LEFT JOIN basic_ranked bi
      ON bi.MAINCODE = ac.MAINCODE
     AND bi.rn = 1
    WHERE bi.MAINCODE IS NOT NULL
      AND COALESCE(bi.OPTIONTYPE, '') NOT LIKE '%回售%'
      AND COALESCE(bi.OPTIONTYPE, '') NOT LIKE '%赎回%'
      AND COALESCE(bi.OPTIONTYPE, '') NOT LIKE '%调整票面利率%'
      AND COALESCE(bi.OPTIONTYPE, '') NOT LIKE '%调息%'
      AND COALESCE(bi.OPTIONTYPE, '') NOT LIKE '%续期%'
      AND bi.RATEADJUSTMODE IS NULL
      AND UPPER(COALESCE(bi.OPMATURITY, '')) NOT LIKE '%N%'
),
concept_flags AS (
    SELECT
        bc.MAINCODE,
        MAX(CASE WHEN cn.CONNATURE = 4041 AND cn.BEGINDATE <= p.as_of_date
                 THEN 1 ELSE 0 END) AS is_lgfv,
        MAX(CASE WHEN cn.CONNATURE IN (4003, 4018, 4036)
                      AND cn.BEGINDATE <= p.as_of_date
                 THEN 1 ELSE 0 END) AS is_green
    FROM active_codes bc
    JOIN bond_conceptnature cn
      ON cn.INNERCODE = bc.INNERCODE
    CROSS JOIN params p
    WHERE cn.CONCEPTTYPE = 40
      AND cn.CONNATURE IN (4003, 4018, 4036, 4041)
      AND (cn.ENDDATE IS NULL OR cn.ENDDATE > p.as_of_date)
      AND cn.INSERTTIME < p.next_day_ts
    GROUP BY bc.MAINCODE
),
rating_per_agency AS (
    SELECT
        maincode,
        rating_desc
    FROM (
        SELECT
            CAST(g.MAINCODE AS UNSIGNED) AS maincode,
            g.CRDESC AS rating_desc,
            g.CRSTATUS,
            g.RATINGEFFECTIVEENDDATE,
            ROW_NUMBER() OVER (
                PARTITION BY g.MAINCODE, g.CRASCODE
                ORDER BY g.INFOPUBLDATE DESC, g.CRDATE DESC, g.UPDATETIME DESC, g.ID DESC
            ) AS rn
        FROM active_maincodes am
        JOIN bond_bdcreditgrading g
          ON g.MAINCODE = am.MAINCODE
        CROSS JOIN params p
        WHERE g.INFOPUBLDATE < p.next_day_ts
          AND (g.CRDATE IS NULL OR g.CRDATE < p.next_day_ts)
          AND g.CRTYPECODE = 2
          AND g.CRSYSTEM = 1
    ) ranked
    CROSS JOIN params p
    -- A later invalid record must not revive an older agency rating.
    WHERE rn = 1
      AND CRSTATUS = 1
      AND (RATINGEFFECTIVEENDDATE IS NULL OR RATINGEFFECTIVEENDDATE >= p.as_of_date)
      AND rating_desc IN (
          'AAA+', 'AAA', 'AAA-', 'AA+', 'AA', 'AA-',
          'A+', 'A', 'A-', 'BBB+', 'BBB', 'BBB-',
          'BB+', 'BB', 'BB-', 'B+', 'B', 'B-',
          'CCC+', 'CCC', 'CCC-', 'CC', 'C', 'D'
      )
),
lowest_rating AS (
    SELECT maincode, rating_desc
    FROM (
        SELECT
            maincode,
            rating_desc,
            ROW_NUMBER() OVER (
                PARTITION BY maincode
                ORDER BY
                    CASE rating_desc
                        WHEN 'AAA+' THEN 0 WHEN 'AAA' THEN 1 WHEN 'AAA-' THEN 2
                        WHEN 'AA+' THEN 3 WHEN 'AA' THEN 4 WHEN 'AA-' THEN 5
                        WHEN 'A+' THEN 6 WHEN 'A' THEN 7 WHEN 'A-' THEN 8
                        WHEN 'BBB+' THEN 9 WHEN 'BBB' THEN 10 WHEN 'BBB-' THEN 11
                        WHEN 'BB+' THEN 12 WHEN 'BB' THEN 13 WHEN 'BB-' THEN 14
                        WHEN 'B+' THEN 15 WHEN 'B' THEN 16 WHEN 'B-' THEN 17
                        WHEN 'CCC+' THEN 18 WHEN 'CCC' THEN 19 WHEN 'CCC-' THEN 20
                        WHEN 'CC' THEN 21 WHEN 'C' THEN 22 WHEN 'D' THEN 23
                        ELSE 999
                    END DESC,
                    rating_desc
            ) AS rn
        FROM rating_per_agency
    ) ranked
    WHERE rn = 1
),
valuation_ranked AS (
    SELECT
        ac.MAINCODE,
        ac.INNERCODE,
        ac.bond_code,
        ac.bond_name,
        ac.bond_full_name,
        ac.issuer,
        lr.rating_desc,
        cf.is_green,
        v.VPYIELD AS valuation_yield_pct,
        v.TRUEREMAINMATURITY AS remaining_years,
        ROW_NUMBER() OVER (
            PARTITION BY ac.MAINCODE
            ORDER BY
                COALESCE(ac.bond_code, ''),
                ac.INNERCODE,
                v.ID DESC
        ) AS rn
    FROM active_codes ac
    CROSS JOIN params p
    JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL)
      ON v.INNERCODE = ac.INNERCODE
     AND v.ENDDATE >= p.as_of_date
     AND v.ENDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    JOIN clean_maincodes cm
      ON cm.MAINCODE = ac.MAINCODE
    JOIN concept_flags cf
     ON cf.MAINCODE = ac.MAINCODE
     AND cf.is_lgfv = 1
     AND cf.is_green = 1
    JOIN lowest_rating lr
      ON lr.maincode = ac.MAINCODE
    WHERE ac.SECUMARKET = 89
      AND v.CREDIBILITYCODE = 1
      AND v.YIELDCODE = 2
      AND v.VPYIELD IS NOT NULL
      AND v.TRUEREMAINMATURITY IS NOT NULL
      AND v.TRUEREMAINMATURITY > 1
      AND v.TRUEREMAINMATURITY <= 3
      AND lr.rating_desc = 'AAA'
      AND v.INSERTTIME < p.next_day_ts
)
SELECT
    CAST(MAINCODE AS UNSIGNED) AS maincode,
    CAST(INNERCODE AS UNSIGNED) AS innercode,
    bond_code,
    bond_name,
    bond_full_name,
    issuer,
    rating_desc,
    CAST(is_green AS UNSIGNED) AS is_green,
    remaining_years,
    valuation_yield_pct
FROM valuation_ranked
WHERE rn = 1
ORDER BY MAINCODE;
