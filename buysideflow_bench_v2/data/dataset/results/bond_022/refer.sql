WITH RECURSIVE params AS (
    SELECT DATE('2026-01-05') AS start_date, DATE('2026-01-23') AS end_date
), dates AS (
    SELECT start_date AS val_date FROM params
    UNION ALL
    SELECT DATE_ADD(d.val_date, INTERVAL 1 DAY)
    FROM dates d JOIN params p ON d.val_date < p.end_date
), urban_dates AS (
    SELECT DISTINCT d.val_date, b.MAINCODE
    FROM dates d
    JOIN bond_conceptnature c
      ON c.CONCEPTTYPE = 40 AND c.CONNATURE = 4041
     AND c.BEGINDATE < DATE_ADD(d.val_date, INTERVAL 1 DAY)
     AND (c.ENDDATE IS NULL OR c.ENDDATE > d.val_date)
     AND c.INSERTTIME < DATE_ADD(d.val_date, INTERVAL 1 DAY)
    JOIN bond_code b ON b.INNERCODE = c.INNERCODE
    WHERE b.MAINCODE IS NOT NULL
), candidate_codes AS (
    -- bond_code has no MAINCODE index. Materialize the bounded code universe
    -- once so MySQL can index this derived table instead of rescanning quotes.
    SELECT DISTINCT b.MAINCODE,b.INNERCODE,b.SECUCODE,b.SECUABBR,
        COALESCE(t.ISSUEDATE,t.VALUEDATE,b.LISTEDDATE) AS start_date,
        b.INTERESTENDDATE,b.DELISTDATE,t.EARLYMATURITYDATE
    FROM bond_code b LEFT JOIN bond_basicinfo t ON t.MAINCODE=b.MAINCODE
    WHERE COALESCE(t.ISSUEDATE,t.VALUEDATE,b.LISTEDDATE) < '2026-01-24'
      AND (b.INTERESTENDDATE IS NULL OR b.INTERESTENDDATE >= '2026-01-05')
      AND EXISTS (SELECT 1 FROM urban_dates u WHERE u.MAINCODE=b.MAINCODE)
), rating_ranked AS (
    SELECT u.val_date, r.MAINCODE, r.CRDESC,
        ROW_NUMBER() OVER (
            PARTITION BY u.val_date, r.MAINCODE
            ORDER BY r.INFOPUBLDATE DESC, r.CRDATE DESC, r.ID DESC
        ) AS rn
    FROM urban_dates u
    JOIN bond_bdcreditgrading r ON r.MAINCODE = u.MAINCODE
    WHERE r.INFOPUBLDATE < DATE_ADD(u.val_date, INTERVAL 1 DAY)
      AND r.CRDATE < DATE_ADD(u.val_date, INTERVAL 1 DAY)
      AND (r.RATINGEFFECTIVEENDDATE IS NULL OR r.RATINGEFFECTIVEENDDATE >= u.val_date)
      AND r.CRTYPECODE = 2 AND r.CRSYSTEM = 1 AND r.CRSTATUS = 1
), current_ranked AS (
    SELECT r.val_date, b.MAINCODE, b.INNERCODE, b.SECUCODE, b.SECUABBR,
        v.VPYIELD, v.VALUECLEANPRICE, v.TRUEREMAINMATURITY,
        ROW_NUMBER() OVER (
            PARTITION BY r.val_date, b.INNERCODE
            ORDER BY v.INSERTTIME DESC, v.UPDATETIME DESC, v.ID DESC
        ) AS rn
    FROM rating_ranked r
    STRAIGHT_JOIN candidate_codes b ON b.MAINCODE = r.MAINCODE
    STRAIGHT_JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL)
      ON v.INNERCODE = b.INNERCODE AND v.ENDDATE = r.val_date
    WHERE r.rn = 1 AND r.CRDESC IN ('AAA', 'AA+')
      AND v.CREDIBILITYCODE = 1 AND v.YIELDCODE = 2
      AND v.INSERTTIME < DATE_ADD(r.val_date, INTERVAL 1 DAY)
      AND b.start_date < DATE_ADD(r.val_date, INTERVAL 1 DAY)
      AND (b.INTERESTENDDATE IS NULL OR b.INTERESTENDDATE >= r.val_date)
      AND (b.DELISTDATE IS NULL OR b.DELISTDATE >= r.val_date)
      AND (b.EARLYMATURITYDATE IS NULL OR b.EARLYMATURITYDATE >= r.val_date)
), current_sample AS (
    SELECT * FROM current_ranked
    WHERE rn = 1 AND TRUEREMAINMATURITY BETWEEN 1 AND 5
      AND VPYIELD IS NOT NULL AND VALUECLEANPRICE IS NOT NULL
), previous_ranked AS (
    -- Select both observations using information available on the check date.
    SELECT c.val_date, c.INNERCODE, DATE(v.ENDDATE) AS prev_val_date,
        v.VPYIELD AS prev_yield, v.VALUECLEANPRICE AS prev_cleanprice,
        ROW_NUMBER() OVER (
            PARTITION BY c.val_date, c.INNERCODE, DATE(v.ENDDATE)
            ORDER BY v.INSERTTIME DESC, v.UPDATETIME DESC, v.ID DESC
        ) AS version_rn
    FROM current_sample c
    JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL) ON v.INNERCODE = c.INNERCODE
     AND v.ENDDATE >= DATE_SUB(c.val_date, INTERVAL 5 DAY)
     AND v.ENDDATE < c.val_date
    WHERE v.CREDIBILITYCODE = 1 AND v.YIELDCODE = 2
      AND v.INSERTTIME < DATE_ADD(c.val_date, INTERVAL 1 DAY)
), previous_valid AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY val_date, INNERCODE ORDER BY prev_val_date DESC
    ) AS day_rn
    FROM previous_ranked
    WHERE version_rn = 1 AND prev_yield IS NOT NULL AND prev_cleanprice IS NOT NULL
)
SELECT c.val_date, c.MAINCODE, c.INNERCODE, c.SECUCODE, c.SECUABBR,
    c.VPYIELD, c.VALUECLEANPRICE, p.prev_val_date, p.prev_yield, p.prev_cleanprice
FROM current_sample c
JOIN previous_valid p ON p.val_date = c.val_date AND p.INNERCODE = c.INNERCODE AND p.day_rn = 1
ORDER BY c.val_date, c.SECUCODE;
