WITH code_ranked AS (
    SELECT
        MAINCODE,
        SECUABBR,
        SECUCODE,
        ROW_NUMBER() OVER (
            PARTITION BY MAINCODE
            ORDER BY SECUCODE ASC, INNERCODE ASC
        ) AS rn
    FROM bond_code
    WHERE MAINCODE IS NOT NULL
      AND SECUABBR IS NOT NULL
      AND SECUCODE IS NOT NULL
),
issue_ranked AS (
    SELECT
        MAINCODE,
        ISSUER,
        PLANISSUESIZE,
        ACTUALISSUESIZE,
        DATE(ISSUEDATESTART) AS issue_start_date,
        (ACTUALISSUESIZE - PLANISSUESIZE) / NULLIF(PLANISSUESIZE, 0) AS deviation_ratio,
        ROW_NUMBER() OVER (
            PARTITION BY MAINCODE, DATE(ISSUEDATESTART)
            ORDER BY UPDATETIME DESC, ID DESC
        ) AS rn
    FROM bond_issuenew
    WHERE ISSUEDATESTART >= '2024-01-01'
      AND ISSUEDATESTART < '2025-01-01'
)
SELECT
    CAST(ib.MAINCODE AS CHAR) AS `债券主代码`,
    cr.SECUABBR AS `债券简称`,
    ib.ISSUER AS `发行人`,
    ib.issue_start_date AS `发行起始日`,
    ROUND(ib.PLANISSUESIZE, 2) AS `计划发行规模(百万元)`,
    ROUND(ib.ACTUALISSUESIZE, 2) AS `实际发行规模(百万元)`,
    ROUND(ib.deviation_ratio * 100, 2) AS `偏差率(%)`
FROM issue_ranked ib
JOIN code_ranked cr
  ON ib.MAINCODE = cr.MAINCODE
 AND cr.rn = 1
WHERE ib.rn = 1
  AND ib.PLANISSUESIZE > 0
  AND ib.ACTUALISSUESIZE IS NOT NULL
ORDER BY ABS(ib.deviation_ratio) DESC, ib.MAINCODE ASC, ib.issue_start_date ASC
LIMIT 10;
