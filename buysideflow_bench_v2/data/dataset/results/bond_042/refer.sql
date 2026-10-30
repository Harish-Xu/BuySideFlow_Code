WITH params AS (
    SELECT DATE('2025-05-31') AS as_of_date,
           DATE_SUB(DATE('2025-05-31'), INTERVAL 90 DAY) AS hist_target_date
), valuation_days AS (
    -- The common vendor date is chosen before checking this issuer's coverage.
    SELECT 'current' AS endpoint, DATE('2025-05-31') AS target_date,
        (SELECT v.ENDDATE FROM bond_cbvaluationall v
         WHERE v.ENDDATE < '2025-06-01' AND v.INSERTTIME < '2025-06-01'
         ORDER BY v.ENDDATE DESC LIMIT 1) AS val_date
    UNION ALL
    SELECT 'previous', DATE('2025-03-02'),
        (SELECT v.ENDDATE FROM bond_cbvaluationall v
         WHERE v.ENDDATE < '2025-03-03' AND v.INSERTTIME < '2025-06-01'
         ORDER BY v.ENDDATE DESC LIMIT 1)
), candidates AS (
    SELECT b.*, COALESCE(t.ISSUEDATE,t.VALUEDATE,b.LISTEDDATE) AS start_date
    FROM bond_code b LEFT JOIN bond_basicinfo t ON t.MAINCODE=b.MAINCODE
    CROSS JOIN params p
    WHERE b.COMPANYCODE = 6 AND b.ISSUER = '万科企业股份有限公司'
      AND b.MAINCODE IS NOT NULL AND b.SECUMARKET IN (83, 89, 90)
      AND (b.BONDTYPELEVEL1 IN (1600,1700,1800,2500,2600,2800,2900,3100,3200,3300,3700)
           OR b.BONDTYPELEVEL2 IN (1901,1903))
      AND COALESCE(t.ISSUEDATE,t.VALUEDATE,b.LISTEDDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
), live_maincodes AS (
    SELECT DISTINCT b.MAINCODE
    FROM candidates b CROSS JOIN params p
    WHERE b.start_date < DATE_ADD(p.hist_target_date, INTERVAL 1 DAY)
      AND b.INTERESTENDDATE >= p.as_of_date
      AND (b.DELISTDATE IS NULL OR b.DELISTDATE >= p.as_of_date)
      AND NOT EXISTS (
          SELECT 1 FROM bond_basicinfo t WHERE t.MAINCODE = b.MAINCODE
            AND t.EARLYMATURITYDATE < p.as_of_date
      )
), valuation_versions AS (
    SELECT d.endpoint, d.val_date, b.MAINCODE, b.INNERCODE, b.SECUCODE,
           b.SECUMARKET, v.VPYIELD,
           ROW_NUMBER() OVER (
               PARTITION BY d.endpoint, b.INNERCODE
               ORDER BY v.INSERTTIME DESC, v.UPDATETIME DESC, v.ID DESC
           ) AS version_rn
    FROM valuation_days d
    JOIN bond_cbvaluationall v ON v.ENDDATE = d.val_date
    JOIN candidates b ON b.INNERCODE = v.INNERCODE
    JOIN live_maincodes c ON c.MAINCODE = b.MAINCODE
    CROSS JOIN params p
    WHERE v.CREDIBILITYCODE = 1 AND v.YIELDCODE = 2
      AND v.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND b.start_date < DATE_ADD(d.target_date, INTERVAL 1 DAY)
), market_ranked AS (
    SELECT *, ROW_NUMBER() OVER (
        PARTITION BY endpoint, MAINCODE
        ORDER BY CASE SECUMARKET WHEN 89 THEN 0 ELSE 1 END, SECUCODE, INNERCODE
    ) AS market_rn
    FROM valuation_versions WHERE version_rn = 1 AND VPYIELD IS NOT NULL
)
SELECT c.MAINCODE, c.val_date AS current_val_date, h.val_date AS previous_val_date,
       c.VPYIELD AS current_yield, h.VPYIELD AS previous_yield
FROM market_ranked c JOIN market_ranked h ON h.MAINCODE = c.MAINCODE
WHERE c.endpoint = 'current' AND h.endpoint = 'previous'
  AND c.market_rn = 1 AND h.market_rn = 1
ORDER BY c.MAINCODE;
