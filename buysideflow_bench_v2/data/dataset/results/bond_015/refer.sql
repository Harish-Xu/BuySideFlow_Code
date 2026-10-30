WITH params AS (
    SELECT
        DATE('2025-12-29') AS rating_start_date,
        DATE('2026-01-05') AS rating_end_exclusive,
        DATE('2026-01-06') AS as_of_exclusive
)
SELECT
    COALESCE(NULLIF(ia.ABBRCHINAME, ''), c.COMPANYNAME) AS `发债主体简称`,
    DATE_FORMAT(c.CRDATE, '%Y-%m-%d') AS `评级下调日期`,
    DATE_FORMAT(c.LASTCRDATE, '%Y-%m-%d') AS `上次评级日期`,
    c.LASTCRDESC AS `下调前评级`,
    c.CRDESC AS `下调后评级`
FROM bond_comcreditgrading c
JOIN params p
  ON c.CRDATE >= p.rating_start_date
 AND c.CRDATE < p.rating_end_exclusive
 AND c.INFOPUBLDATE < p.as_of_exclusive
LEFT JOIN lc_instiarchive ia
  ON ia.COMPANYCODE = c.COMPANYCODE
WHERE c.CRCHANGE = '调低'
  AND c.CRSTATUS = 1
  AND c.CRTYPE IN (
      3,
      100, 101, 102, 103,
      200, 201, 202, 203,
      300, 301, 302, 303, 312, 314,
      400, 401,
      500, 501
  )
  AND EXISTS (
      SELECT 1
      FROM bond_code b
      LEFT JOIN bond_basicinfo bi
        ON bi.MAINCODE = b.MAINCODE
      WHERE b.COMPANYCODE = c.COMPANYCODE
        AND COALESCE(bi.VALUEDATE, bi.ISSUEDATE, b.LISTEDDATE) <= c.CRDATE
        AND COALESCE(
                bi.EARLYMATURITYDATE,
                bi.REDEMPTIONDATE,
                b.INTERESTENDDATE,
                bi.ENDDATE
            ) >= c.CRDATE
  )
ORDER BY c.CRDATE DESC, `发债主体简称` ASC;
