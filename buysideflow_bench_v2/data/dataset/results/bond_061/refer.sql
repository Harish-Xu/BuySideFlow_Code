-- Candidate pool for bond_061.
-- bond_code.MAINCODE links the same bond across different market INNERCODEs.
-- Listing market mapping from gildata_schema.json:
--   89=银行间债券市场, 83=上交所, 90=深交所, 18=北交所.
-- bond_basicquote has exchange-side quote rows with SECUMARKET = 12 for some
-- SSE-listed bonds, even though generic market dictionaries elsewhere do not
-- enumerate it; 12 is handled only on the quote side in refer.py.
-- PIT listing filter as of 2024-06-30 uses LISTEDDATE, INTERESTENDDATE and DELISTDATE.

WITH active_codes AS (
    SELECT
        MAINCODE,
        INNERCODE,
        SECUCODE,
        SECUABBR,
        SECUMARKET,
        CASE
            WHEN SECUMARKET = 89 THEN 'bank'
            WHEN SECUMARKET = 83 THEN 'exchange'
        END AS market_group
    FROM bond_code
    WHERE MAINCODE IS NOT NULL
      AND INNERCODE IS NOT NULL
      AND SECUCODE IS NOT NULL
      AND SECUABBR IS NOT NULL
      AND SECUMARKET IN (89, 83)
      AND (LISTEDDATE IS NULL OR DATE(LISTEDDATE) <= DATE('2024-06-30'))
      AND (INTERESTENDDATE IS NULL OR DATE(INTERESTENDDATE) > DATE('2024-06-30'))
      AND (DELISTDATE IS NULL OR DATE(DELISTDATE) > DATE('2024-06-30'))
),
cross_market_main AS (
    SELECT MAINCODE
    FROM active_codes
    GROUP BY MAINCODE
    HAVING SUM(CASE WHEN market_group = 'bank' THEN 1 ELSE 0 END) > 0
       AND SUM(CASE WHEN market_group = 'exchange' THEN 1 ELSE 0 END) > 0
)
SELECT
    c.MAINCODE,
    c.INNERCODE,
    c.SECUCODE,
    c.SECUABBR,
    c.SECUMARKET,
    c.market_group
FROM active_codes c
JOIN cross_market_main m
  ON c.MAINCODE = m.MAINCODE
ORDER BY c.MAINCODE, c.market_group, c.SECUMARKET, c.SECUCODE;
