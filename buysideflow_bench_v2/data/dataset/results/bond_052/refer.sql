-- refer.py executes the following focused extractions and performs PIT rating
-- selection, curve interpolation, 20th-valuation-day selection and aggregation
-- in Python.

-- 1) Fixed-rate, par 2024Q4 MTNs with issue size >= RMB 500mn and 1-5Y term.
--    Q4 is determined by ISSUEDATE rather than the announcement date.
WITH issue_ranked AS (
    SELECT
        n.*,
        ROW_NUMBER() OVER (
            PARTITION BY n.MAINCODE
            ORDER BY n.PUBLICANNOUNCEMENTDATE DESC, n.ID DESC
        ) AS rn
    FROM bond_issuenew n
    WHERE n.PUBLICANNOUNCEMENTDATE < '2025-04-01'
)
SELECT
    b.MAINCODE,
    b.INNERCODE,
    DATE(b.ISSUEDATE) AS issue_date,
    DATE(n.PUBLICANNOUNCEMENTDATE) AS announcement_date,
    DATE(COALESCE(b.LISTEDDATE, n.LISTEDDATEINBANK, n.LISTEDDATEINEXCHANGE)) AS listed_date,
    b.COUPONRATE AS issue_rate_pct,
    COALESCE(n.ISSUEPRICE, b.ISSUEPRICE) AS issue_price,
    b.MATURITY AS issue_maturity,
    COALESCE(n.ACTUALISSUESIZE / 100.0, b.ACTUALISSUESIZE) AS issue_size_yi
FROM bond_basicinfo b
JOIN issue_ranked n
  ON b.MAINCODE = n.MAINCODE
 AND n.rn = 1
WHERE b.ISSUEDATE >= '2024-10-01'
  AND b.ISSUEDATE < '2025-01-01'
  AND b.BONDNATURE = 14
  AND b.RATEIFFLOAT = 2
  AND COALESCE(n.ISSUEPRICE, b.ISSUEPRICE) = 100
  AND COALESCE(n.ACTUALISSUESIZE / 100.0, b.ACTUALISSUESIZE) >= 5
  AND b.MATURITY > 1
  AND b.MATURITY <= 5
  AND COALESCE(b.OPMATURITY, '') NOT LIKE '%N%'
  AND COALESCE(b.BONDFULLNAME, '') NOT LIKE '%永续%'
  AND COALESCE(b.OPTIONTYPE, '') NOT LIKE '%续期%';

-- 2) Domestic long-term bond ratings. Python selects the latest rating that
--    had been published and was effective on each bond's issue date.
SELECT
    MAINCODE,
    CRDESC,
    DATE(INFOPUBLDATE) AS rating_pub_date,
    DATE(CRDATE) AS rating_date,
    DATE(RATINGEFFECTIVEENDDATE) AS rating_end_date,
    INSERTTIME,
    ID
FROM bond_bdcreditgrading
WHERE CRTYPECODE = 2
  AND CRSYSTEM = 1
  AND CRSTATUS = 1
  AND CRDESC IS NOT NULL;

-- 3) ChinaBond MTN curves. 68 is AA+ and 69 is AAA. The initial curve is the
--    latest curve date strictly before the issue announcement; the observation
--    curve uses the exact 20th valuation date.
SELECT
    DATE(ENDDATE) AS curve_date,
    CURVECODE AS curve_code,
    YEARSTOMATURITY AS tenor,
    YIELD * 100.0 AS curve_yield_pct
FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
WHERE CURVECODE IN (68, 69)
  AND YIELDTYPECODE = 1
  AND YEARSTOMATURITY > 0
  AND YEARSTOMATURITY <= 5
  AND YIELD IS NOT NULL
  AND UPDATETIME < DATE_ADD(DATE(ENDDATE), INTERVAL 1 DAY);

-- 4) Same-day ChinaBond recommended valuations. The 20th distinct valuation
--    date from LISTEDDATE is the observation point; TRUEREMAINMATURITY supplies
--    the term used to interpolate that day's rating curve.
SELECT
    INNERCODE,
    DATE(ENDDATE) AS valuation_date,
    VPYIELD AS valuation_yield_pct,
    TRUEREMAINMATURITY AS remaining_maturity
FROM bond_cbvaluationall FORCE INDEX (IX_BOND_CBVALUATIONALL)
WHERE CREDIBILITYCODE = 1
  AND VPYIELD IS NOT NULL
  AND (INSERTTIME IS NULL OR INSERTTIME < DATE_ADD(DATE(ENDDATE), INTERVAL 1 DAY))
  AND UPDATETIME < DATE_ADD(DATE(ENDDATE), INTERVAL 1 DAY);
