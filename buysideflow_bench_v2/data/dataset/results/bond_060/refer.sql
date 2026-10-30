-- bond_060 is implemented as SQL+Python:
-- 1) SQL extracts the Q4-2024 fixed-rate par MTN candidates and issue-date
--    PIT ratings.
-- 2) Python selects the top 10 by actual issue size and linearly interpolates
--    the same-rating curve from the latest ChinaBond curve date before each
--    bond's public announcement date.
-- Fixed-rate filter uses gildata_schema.json enum:
--   RATEIFFLOAT = 2 means non-floating rate, and COMPOUNDMETHOD = 1 means
--   single-interest fixed-rate coupon.
--
-- Curve code mapping from gildata_schema.json:
--   68 = 中债中短期票据收益率曲线(AA+)
--   69 = 中债中短期票据收益率曲线(AAA)

WITH code_ranked AS (
    SELECT
        bc.MAINCODE,
        bc.INNERCODE,
        bc.SECUMARKET,
        bc.SECUCODE,
        bc.SECUABBR,
        bc.CHINAME,
        bc.ISSUER,
        bc.BONDTYPELEVEL1,
        bc.BONDTYPELEVEL1DESC,
        bc.BONDTYPELEVEL2,
        bc.BONDTYPELEVEL2DESC,
        ROW_NUMBER() OVER (
            PARTITION BY bc.MAINCODE
            ORDER BY
                CASE WHEN bc.SECUMARKET = 89 THEN 0 ELSE 1 END,
                COALESCE(bc.SECUCODE, ''),
                bc.INNERCODE
        ) AS rn
    FROM bond_code bc
    WHERE bc.MAINCODE IS NOT NULL
      AND bc.SECUCODE IS NOT NULL
      AND bc.SECUABBR IS NOT NULL
      AND (bc.BONDTYPELEVEL1 = 1800 OR bc.BONDTYPELEVEL2 = 1800)
),
issue_ranked AS (
    SELECT
        n.MAINCODE,
        n.ISSUEPRICE,
        n.ACTUALISSUESIZE / 100.0 AS actual_issue_size_yi,
        DATE(n.PUBLICANNOUNCEMENTDATE) AS announcement_date,
        n.ID,
        ROW_NUMBER() OVER (
            PARTITION BY n.MAINCODE
            ORDER BY n.PUBLICANNOUNCEMENTDATE DESC, n.ID DESC
        ) AS rn
    FROM bond_issuenew n
    WHERE n.PUBLICANNOUNCEMENTDATE <= DATE('2025-01-03')
),
new_issue_candidates AS (
    SELECT
        bi.MAINCODE,
        co.SECUCODE,
        co.SECUABBR,
        COALESCE(co.ISSUER, bi.ISSUER) AS ISSUER,
        DATE(bi.ISSUEDATE) AS issue_date,
        ir.announcement_date,
        COALESCE(bi.ACTUALISSUESIZE, ir.actual_issue_size_yi) AS issue_size_yi,
        bi.COUPONRATE AS issue_rate_pct,
        bi.MATURITY AS issue_maturity
    FROM bond_basicinfo bi
    JOIN code_ranked co
      ON bi.MAINCODE = co.MAINCODE
     AND co.rn = 1
    LEFT JOIN issue_ranked ir
      ON bi.MAINCODE = ir.MAINCODE
     AND ir.rn = 1
    WHERE bi.ISSUEDATE >= DATE('2024-10-01')
      AND bi.ISSUEDATE < DATE('2025-01-01')
      AND ir.announcement_date IS NOT NULL
      AND COALESCE(bi.ACTUALISSUESIZE, ir.actual_issue_size_yi) IS NOT NULL
      AND bi.COUPONRATE IS NOT NULL
      AND bi.MATURITY > 1
      AND bi.MATURITY <= 5
      AND COALESCE(ir.ISSUEPRICE, bi.ISSUEPRICE) = 100
      AND bi.RATEIFFLOAT = 2
      AND bi.COMPOUNDMETHOD = 1
      AND COALESCE(co.SECUABBR, '') NOT LIKE '%永续%'
      AND COALESCE(co.CHINAME, '') NOT LIKE '%永续%'
      AND COALESCE(bi.BONDFULLNAME, '') NOT LIKE '%永续%'
      AND COALESCE(bi.OPMATURITY, '') NOT LIKE '%N%'
      AND COALESCE(bi.OPTIONTYPE, '') NOT LIKE '%续期%'
      AND COALESCE(bi.OPTIONTYPE, '') NOT LIKE '%利息递延%'
      AND COALESCE(co.SECUABBR, '') NOT LIKE '%次级%'
      AND COALESCE(co.CHINAME, '') NOT LIKE '%次级%'
      AND COALESCE(bi.BONDFULLNAME, '') NOT LIKE '%次级%'
      AND COALESCE(co.BONDTYPELEVEL1DESC, '') NOT LIKE '%次级%'
      AND COALESCE(co.BONDTYPELEVEL2DESC, '') NOT LIKE '%次级%'
),
latest_rating AS (
    SELECT
        r.MAINCODE,
        r.CRDESC,
        ROW_NUMBER() OVER (
            PARTITION BY r.MAINCODE
            ORDER BY r.INFOPUBLDATE DESC, r.CRDATE DESC, r.UPDATETIME DESC, r.ID DESC
        ) AS rn
    FROM bond_bdcreditgrading r
    JOIN new_issue_candidates c
      ON r.MAINCODE = c.MAINCODE
    WHERE r.INFOPUBLDATE < DATE_ADD(c.issue_date, INTERVAL 1 DAY)
      AND r.CRDATE <= c.issue_date
      AND (r.RATINGEFFECTIVEENDDATE IS NULL OR r.RATINGEFFECTIVEENDDATE >= c.issue_date)
      AND (r.INSERTTIME IS NULL OR r.INSERTTIME < DATE_ADD(c.issue_date, INTERVAL 1 DAY))
      AND r.CRTYPECODE = 2
      AND r.CRSYSTEM = 1
      AND r.CRSTATUS = 1
      AND r.CRDESC IN ('AAA', 'AA+')
),
top_issues AS (
SELECT
    c.SECUCODE,
    c.SECUABBR,
    c.ISSUER,
    c.announcement_date,
    c.issue_size_yi,
    r.CRDESC,
    c.issue_maturity,
    c.issue_rate_pct
FROM new_issue_candidates c
JOIN latest_rating r
  ON c.MAINCODE = r.MAINCODE
 AND r.rn = 1
ORDER BY c.issue_size_yi DESC, c.SECUCODE ASC
LIMIT 10
)
SELECT *
FROM top_issues;

-- ChinaBond curve points to be interpolated in Python. For each top issue,
-- keep curve dates strictly before its public announcement date:
--   CURVECODE = 69 for AAA, CURVECODE = 68 for AA+,
--   ENDDATE < announcement_date,
--   YIELDTYPECODE = 1,
-- then use the latest curve date and linearly interpolate by issue_maturity.
