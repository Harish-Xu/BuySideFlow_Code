WITH params AS (
    SELECT DATE('2024-12-31') AS as_of_date
), valuation_day AS (
    SELECT v.ENDDATE AS valuation_date
    FROM bond_cbvaluationall v CROSS JOIN params p
    WHERE v.ENDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND v.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    ORDER BY v.ENDDATE DESC LIMIT 1
), live_codes AS (
    SELECT b.*
    FROM bond_code b LEFT JOIN bond_basicinfo t ON t.MAINCODE=b.MAINCODE
    CROSS JOIN params p
    WHERE b.MAINCODE IS NOT NULL
      AND (b.BONDTYPELEVEL1 IN (1600,1700,1800,2500,2600,2800,2900,3100,3200,3300,3700)
           OR b.BONDTYPELEVEL2 IN (1901,1903))
      AND COALESCE(t.ISSUEDATE,t.VALUEDATE,b.LISTEDDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND (b.INTERESTENDDATE IS NULL OR b.INTERESTENDDATE >= p.as_of_date)
      AND (b.DELISTDATE IS NULL OR b.DELISTDATE >= p.as_of_date)
), excluded_bonds AS (
    SELECT DISTINCT b.MAINCODE
    FROM bond_code b JOIN bond_conceptnature c ON c.INNERCODE = b.INNERCODE
    CROSS JOIN params p
    WHERE c.CONCEPTTYPE = 40 AND c.CONNATURE = 4005
      AND c.BEGINDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND (c.ENDDATE IS NULL OR c.ENDDATE > p.as_of_date)
      AND c.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    UNION
    SELECT MAINCODE FROM live_codes
    WHERE CHINAME LIKE '%永续%' OR CHINAME LIKE '%次级%'
       OR SECUABBR LIKE '%永续%' OR SECUABBR LIKE '%次级%'
    UNION
    SELECT b.MAINCODE FROM bond_default d
    JOIN bond_code b ON b.INNERCODE = d.INNERCODE
    CROSS JOIN params p
    WHERE d.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND COALESCE(d.DEFAULTDATE, d.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND (d.REVOKEDATE IS NULL OR d.REVOKEDATE > p.as_of_date)
      AND (d.DEFAULTPROG IN (201,202,203,801)
           OR (d.DEFAULTTYPE IN (2001,2002,2003,100301) AND COALESCE(d.DEFAULTPROG,0) <> 901))
), candidates AS (
    SELECT b.* FROM live_codes b
    WHERE NOT EXISTS (SELECT 1 FROM excluded_bonds e WHERE e.MAINCODE = b.MAINCODE)
      AND NOT EXISTS (
          SELECT 1 FROM bond_basicinfo t CROSS JOIN params p
          WHERE t.MAINCODE = b.MAINCODE AND t.EARLYMATURITYDATE < p.as_of_date
      )
), representative_codes AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY MAINCODE
        ORDER BY CASE SECUMARKET WHEN 89 THEN 0 ELSE 1 END, SECUCODE, INNERCODE
    ) AS code_rn
    FROM candidates
), valuation_versions AS (
    SELECT b.MAINCODE,b.INNERCODE,b.SECUCODE,b.SECUMARKET,
        v.VPYIELD,v.TRUEREMAINMATURITY,
        ROW_NUMBER() OVER (
            PARTITION BY b.INNERCODE
            ORDER BY v.INSERTTIME DESC,v.UPDATETIME DESC,v.ID DESC
        ) AS version_rn
    FROM candidates b JOIN bond_cbvaluationall v ON v.INNERCODE = b.INNERCODE
    JOIN valuation_day d ON v.ENDDATE = d.valuation_date
    CROSS JOIN params p
    WHERE v.CREDIBILITYCODE = 1 AND v.YIELDCODE = 2
      AND v.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
), valuations AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY MAINCODE
        ORDER BY CASE SECUMARKET WHEN 89 THEN 0 ELSE 1 END, SECUCODE, INNERCODE
    ) AS market_rn
    FROM valuation_versions
    WHERE version_rn = 1 AND VPYIELD IS NOT NULL AND TRUEREMAINMATURITY BETWEEN 1 AND 5
), rating_base AS (
    SELECT r.* FROM bond_bdcreditgrading r CROSS JOIN params p
    WHERE EXISTS (SELECT 1 FROM valuations v WHERE v.MAINCODE = r.MAINCODE AND v.market_rn = 1)
      AND r.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND r.CRDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND r.CRTYPECODE = 2 AND r.CRSYSTEM = 1 AND r.CRSTATUS = 1
      AND r.CRASCODE IS NOT NULL
), first_ratings AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY MAINCODE,CRASCODE ORDER BY INFOPUBLDATE,CRDATE,ID
    ) AS rn FROM rating_base
), current_ratings AS (
    SELECT r.*, ROW_NUMBER() OVER (
        PARTITION BY r.MAINCODE,r.CRASCODE ORDER BY r.INFOPUBLDATE DESC,r.CRDATE DESC,r.ID DESC
    ) AS rn
    FROM rating_base r CROSS JOIN params p
    WHERE r.RATINGEFFECTIVEENDDATE IS NULL OR r.RATINGEFFECTIVEENDDATE >= p.as_of_date
)
SELECT b.SECUCODE AS bond_code,b.SECUABBR AS bond_abbr,c.CRASNAME AS rating_agency,
       v.VPYIELD AS valuation_yield_pct
FROM first_ratings f
JOIN current_ratings c ON c.MAINCODE=f.MAINCODE AND c.CRASCODE=f.CRASCODE AND c.rn=1
JOIN representative_codes b ON b.MAINCODE=f.MAINCODE AND b.code_rn=1
JOIN valuations v ON v.MAINCODE=f.MAINCODE AND v.market_rn=1
WHERE f.rn=1 AND f.CRDESC='AAA' AND c.CRDESC='AA+'
ORDER BY b.SECUCODE,c.CRASNAME,c.CRASCODE;
