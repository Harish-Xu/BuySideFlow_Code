WITH credit_bonds AS (
    SELECT bc.*
    FROM bond_code bc
    WHERE bc.ISSUER = '万科企业股份有限公司'
      AND bc.MAINCODE IS NOT NULL
      AND bc.INNERCODE IS NOT NULL
      AND bc.COMPANYCODE IS NOT NULL
      AND (
            bc.BONDTYPELEVEL1 IN (1600, 1700, 1800, 2500, 2600, 2800, 2900, 3100, 3200, 3300, 3700)
         OR bc.BONDTYPELEVEL2 IN (1901, 1903)
      )
      AND (bc.LISTEDDATE IS NULL OR DATE(bc.LISTEDDATE) <= DATE('2026-01-05'))
),
-- 先按公告日与事件生效日截断，再取同一债券的最新事件。
-- 不预筛展期记录，否则后续公告已撤销全部本金时会回捞旧展期。
debt_events AS (
    SELECT
        bc.MAINCODE,
        d.DEFAULTTYPE,
        d.DEFAULTPROG,
        d.PAYPRINCIPAL,
        ROW_NUMBER() OVER (
            PARTITION BY bc.MAINCODE
            ORDER BY d.INFOPUBLDATE DESC, d.ID DESC
        ) AS rn
    FROM bond_default d
    JOIN (SELECT DISTINCT MAINCODE, INNERCODE FROM credit_bonds) bc
      ON bc.INNERCODE = d.INNERCODE
    WHERE d.INFOPUBLDATE < '2026-01-06'
      AND CASE WHEN d.DEFAULTPROG BETWEEN 301 AND 310
               THEN COALESCE(d.REVOKEDATE, d.INFOPUBLDATE)
               ELSE COALESCE(d.DEFAULTDATE, d.INFOPUBLDATE)
          END < '2026-01-06'
),
-- 2003=本息展期；901=未违约（包括公告宽限期），202/203=本金尚未偿付。
-- 宽限期中的债务不能仅因原到期日或停止交易日已过而排除。
pending_principal AS (
    SELECT MAINCODE
    FROM debt_events
    WHERE rn = 1
      AND DEFAULTTYPE = 2003
      AND DEFAULTPROG IN (901, 202, 203)
      AND PAYPRINCIPAL > 0
)
SELECT DISTINCT
    CAST(bc.MAINCODE AS UNSIGNED) AS maincode,
    CAST(bc.INNERCODE AS UNSIGNED) AS innercode,
    bc.SECUCODE AS bond_code,
    bc.SECUABBR AS bond_name,
    bc.ISSUER AS issuer,
    CAST(bc.COMPANYCODE AS UNSIGNED) AS companycode,
    bc.SECUMARKET AS secumarket
FROM credit_bonds bc
WHERE (
        (bc.INTERESTENDDATE IS NULL OR DATE(bc.INTERESTENDDATE) >= DATE('2026-01-05'))
    AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) >= DATE('2026-01-05'))
)
OR EXISTS (SELECT 1 FROM pending_principal p WHERE p.MAINCODE = bc.MAINCODE)
ORDER BY maincode, secumarket, bond_code, innercode;
