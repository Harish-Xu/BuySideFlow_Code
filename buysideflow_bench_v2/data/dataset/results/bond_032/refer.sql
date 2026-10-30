WITH issue_ranked AS (
    SELECT
        i.*,
        ROW_NUMBER() OVER (
            -- One dated issuance event, not one lifetime total per bond.
            PARTITION BY i.MAINCODE, DATE(i.BIDSTARTDATE)
            ORDER BY i.PUBLICANNOUNCEMENTDATE DESC,
                     i.UPDATETIME DESC,
                     i.ID DESC
        ) AS rn
    FROM bond_issuenew i
    WHERE i.BIDSTARTDATE >= '2025-01-01'
      AND i.BIDSTARTDATE < '2026-01-01'
      AND i.CURRENCY = 1420
      AND i.BONDNATURE IN (14, 6, 19)
),
bank_bonds AS (
    SELECT DISTINCT c.MAINCODE
    FROM bond_code c
    JOIN (SELECT DISTINCT MAINCODE FROM issue_ranked) i
      ON i.MAINCODE = c.MAINCODE
    WHERE c.SECUMARKET = 89
),
issue_base AS (
    SELECT i.BIDSTARTDATE, i.BONDNATURE, i.ACTUALISSUESIZE
    FROM issue_ranked i
    LEFT JOIN bank_bonds b
      ON b.MAINCODE = i.MAINCODE
    WHERE i.rn = 1
      -- A newer missing amount must not revive an older reported amount.
      AND i.ACTUALISSUESIZE IS NOT NULL
      -- Late-year issues may have listing information before bond_code coverage.
      AND (i.LISTEDDATEINBANK IS NOT NULL OR b.MAINCODE IS NOT NULL)
)
SELECT
    DATE_FORMAT(BIDSTARTDATE, '%Y-%m') AS `月份`,
    -- Actual face amount is in millions of yuan and already includes additional placement.
    ROUND(SUM(CASE WHEN BONDNATURE = 14 THEN ACTUALISSUESIZE ELSE 0 END) * 0.01, 2) AS `中期票据`,
    ROUND(SUM(CASE WHEN BONDNATURE IN (6, 19) THEN ACTUALISSUESIZE ELSE 0 END) * 0.01, 2) AS `短融及超短融`
FROM issue_base
GROUP BY DATE_FORMAT(BIDSTARTDATE, '%Y-%m')
ORDER BY `月份`;
