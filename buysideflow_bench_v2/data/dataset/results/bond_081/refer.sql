WITH params AS (
    SELECT
        DATE('2025-01-01') AS disclosure_start_date,
        DATE('2025-12-31') AS as_of_date
),
revoke_events AS (
    SELECT
        CAST(bc.MAINCODE AS UNSIGNED) AS maincode,
        CAST(bc.INNERCODE AS UNSIGNED) AS innercode,
        bc.SECUCODE AS bond_code,
        bc.SECUABBR AS bond_name,
        bc.ISSUER AS issuer,
        DATE(d.DEFAULTDATE) AS default_date,
        DATE(d.REVOKEDATE) AS revoke_date,
        d.INFOTITLE AS revoke_title,
        ROW_NUMBER() OVER (
            PARTITION BY d.INNERCODE
            ORDER BY d.REVOKEDATE ASC, d.INFOPUBLDATE DESC, d.ID DESC
        ) AS rn
    FROM bond_default d FORCE INDEX (IX_BOND_DEFAULT)
    JOIN bond_code bc
      ON d.INNERCODE = bc.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE d.INNERCODE >= 0
      AND d.REVOKEDATE IS NOT NULL
      AND d.REVOKEDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND d.INFOPUBLDATE >= p.disclosure_start_date
      AND d.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
)
SELECT
    maincode,
    innercode,
    bond_code,
    bond_name,
    issuer,
    default_date,
    revoke_date,
    revoke_title
FROM revoke_events
WHERE rn = 1
ORDER BY revoke_date ASC, bond_code ASC;
