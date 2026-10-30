-- Candidate universe at one Q4 signal date; rendered by refer.py.
-- All candidate tests precede future-price retrieval.
WITH
concept AS (
    SELECT DISTINCT CAST(INNERCODE AS UNSIGNED) AS INNERCODE
    FROM bond_conceptnature
    WHERE CONCEPTTYPE = 40 AND CONNATURE = 4041
      AND BEGINDATE <= '{{ signal_date }}'
      AND (ENDDATE IS NULL OR ENDDATE > '{{ signal_date }}')
      AND INSERTTIME < DATE_ADD('{{ signal_date }}', INTERVAL 1 DAY)
),
valuation_versions AS (
    SELECT v.*, bc.MAINCODE,
           ROW_NUMBER() OVER (
               PARTITION BY bc.MAINCODE
               ORDER BY v.INSERTTIME DESC, v.ID DESC, bc.INNERCODE
           ) AS version_rank
    FROM bond_cbvaluationall v
    JOIN bond_code bc ON v.INNERCODE = bc.INNERCODE
    JOIN concept t ON v.INNERCODE = t.INNERCODE
    WHERE v.ENDDATE >= '{{ signal_date }}'
      AND v.ENDDATE < DATE_ADD('{{ signal_date }}', INTERVAL 1 DAY)
      AND v.CREDIBILITYCODE = 1 AND v.YIELDCODE = 2
      AND v.INSERTTIME < DATE_ADD('{{ signal_date }}', INTERVAL 1 DAY)
      AND bc.MAINCODE IS NOT NULL
),
alive_codes AS (
    SELECT DISTINCT bc.MAINCODE, bc.INNERCODE, bc.SECUCODE, bc.SECUMARKET
    FROM bond_code bc
    JOIN (SELECT DISTINCT MAINCODE FROM valuation_versions) u ON bc.MAINCODE = u.MAINCODE
    LEFT JOIN bond_basicinfo bi ON bc.INNERCODE = bi.INNERCODE
    WHERE (
        bc.LISTEDDATE <= '{{ signal_date }}'
        OR (bi.INITIALINFOPUBLDATE < DATE_ADD('{{ signal_date }}', INTERVAL 1 DAY)
            AND COALESCE(bi.ISSUEDATE, bi.VALUEDATE) <= '{{ signal_date }}')
    )
      AND COALESCE(bc.INTERESTENDDATE, bi.ENDDATE) > '{{ signal_date }}'
      AND (bc.DELISTDATE IS NULL OR bc.DELISTDATE > '{{ signal_date }}')
),
codes AS (
    SELECT MAINCODE,
           COALESCE(MIN(CASE WHEN SECUMARKET = 89 THEN SECUCODE END), MIN(SECUCODE)) AS sort_code
    FROM alive_codes
    GROUP BY MAINCODE
),
base AS (
    SELECT v.MAINCODE, v.VPYIELD, v.TRUEREMAINMATURITY, c.sort_code
    FROM valuation_versions v JOIN codes c ON v.MAINCODE = c.MAINCODE
    WHERE v.version_rank = 1 AND v.VPYIELD IS NOT NULL AND c.sort_code IS NOT NULL
      AND v.TRUEREMAINMATURITY > 3 AND v.TRUEREMAINMATURITY <= 5
),
rating_versions AS (
    SELECT r.*,
           ROW_NUMBER() OVER (
               PARTITION BY r.MAINCODE, COALESCE(CAST(r.CRASCODE AS CHAR), r.CRASNAME)
               ORDER BY r.INFOPUBLDATE DESC, r.CRDATE DESC, r.ID DESC
           ) AS agency_rank
    FROM bond_bdcreditgrading r JOIN base b ON r.MAINCODE = b.MAINCODE
    WHERE r.INFOPUBLDATE < DATE_ADD('{{ signal_date }}', INTERVAL 1 DAY)
      AND r.CRDATE < DATE_ADD('{{ signal_date }}', INTERVAL 1 DAY)
      AND r.CRTYPECODE = 2 AND r.CRSYSTEM = 1
),
ratings AS (
    SELECT MAINCODE, MIN(CRCODE) AS worst_rating, COUNT(*) AS agency_count
    FROM rating_versions
    WHERE agency_rank = 1 AND CRSTATUS = 1
      AND (RATINGEFFECTIVEENDDATE IS NULL OR RATINGEFFECTIVEENDDATE >= '{{ signal_date }}')
    GROUP BY MAINCODE
    HAVING COUNT(*) = COUNT(CRCODE) AND MIN(CRCODE) = 980
)
SELECT DATE('{{ signal_date }}') AS signal_date, b.MAINCODE, b.sort_code,
       b.VPYIELD, b.TRUEREMAINMATURITY, r.worst_rating, r.agency_count
FROM base b JOIN ratings r ON b.MAINCODE = r.MAINCODE
ORDER BY b.VPYIELD DESC, b.sort_code, b.MAINCODE;
