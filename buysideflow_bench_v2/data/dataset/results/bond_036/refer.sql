WITH RECURSIVE months AS (
    SELECT DATE('2024-01-01') AS month_start
    UNION ALL
    SELECT DATE_ADD(month_start, INTERVAL 1 MONTH)
    FROM months
    WHERE month_start < DATE('2024-12-01')
),
credit_bonds AS (
    SELECT
        bc.MAINCODE,
        COALESCE(CAST(MAX(bc.COMPANYCODE) AS CHAR), MAX(bc.ISSUER)) AS issuer_key
    FROM bond_code bc
    WHERE (
            bc.BONDTYPELEVEL1 IN (1600, 1700, 1800, 2500, 2600, 2800, 2900, 3100, 3200, 3300, 3700)
            OR bc.BONDTYPELEVEL2 IN (1901, 1903)
          )
      AND bc.MAINCODE IS NOT NULL
    GROUP BY bc.MAINCODE
),
credit_issuers AS (
    SELECT DISTINCT
        CAST(bc.COMPANYCODE AS CHAR) AS issuer_key
    FROM bond_code bc
    WHERE (
            bc.BONDTYPELEVEL1 IN (1600, 1700, 1800, 2500, 2600, 2800, 2900, 3100, 3200, 3300, 3700)
            OR bc.BONDTYPELEVEL2 IN (1901, 1903)
          )
      AND bc.COMPANYCODE IS NOT NULL
),
downgrade_bonds AS (
    SELECT DISTINCT
        DATE_FORMAT(g.INFOPUBLDATE, '%Y-%m') AS ym,
        g.MAINCODE
    FROM bond_bdcreditgrading g
    JOIN credit_bonds cb
      ON cb.MAINCODE = g.MAINCODE
    WHERE g.INFOPUBLDATE >= DATE('2024-01-01')
      AND g.INFOPUBLDATE < DATE('2025-01-01')
      AND g.MAINCODE IS NOT NULL
      AND g.CRTYPECODE = 2
      AND g.CRCHANGE = '调低'
      AND g.CRSYSTEM = 1
      AND g.CRSTATUS = 1
),
downgrade_month AS (
    SELECT
        ym,
        COUNT(DISTINCT MAINCODE) AS downgrade_bond_cnt
    FROM downgrade_bonds
    GROUP BY ym
),
downgrade_issuer_month AS (
    SELECT
        d.ym,
        COUNT(DISTINCT cb.issuer_key) AS downgrade_issuer_cnt
    FROM downgrade_bonds d
    JOIN credit_bonds cb
      ON cb.MAINCODE = d.MAINCODE
    GROUP BY d.ym
),
subject_pairs AS (
    SELECT DISTINCT
        g.COMPANYCODE,
        g.CRASCODE
    FROM bond_comcreditgrading g
    JOIN credit_issuers ci
      ON ci.issuer_key = CAST(g.COMPANYCODE AS CHAR)
    WHERE g.INFOPUBLDATE >= DATE('2024-01-01')
      AND g.INFOPUBLDATE < DATE('2025-01-01')
      AND g.CRASCODE IS NOT NULL
      AND g.CRTYPE = 3
      AND g.CRSYSTEM = 1
      AND g.CRSTATUS = 1
      AND g.CROUTLOOK IN (1203, 1208)
),
subject_daily AS (
    SELECT
        g.COMPANYCODE,
        g.CRASCODE,
        DATE(g.INFOPUBLDATE) AS publish_date,
        -- A date-only publication cannot establish intraday ordering.
        CASE WHEN COUNT(g.CROUTLOOK) = COUNT(*)
                   AND COUNT(DISTINCT g.CROUTLOOK) = 1
             THEN MIN(g.CROUTLOOK)
             ELSE NULL END AS CROUTLOOK
    FROM bond_comcreditgrading g
    JOIN subject_pairs p
      ON p.COMPANYCODE = g.COMPANYCODE
     AND p.CRASCODE = g.CRASCODE
    WHERE g.INFOPUBLDATE < DATE('2025-01-01')
      AND g.CRTYPE = 3
      AND g.CRSYSTEM = 1
      AND g.CRSTATUS = 1
    GROUP BY g.COMPANYCODE, g.CRASCODE, DATE(g.INFOPUBLDATE)
),
subject_history AS (
    SELECT
        d.*,
        LAG(d.CROUTLOOK) OVER (
            PARTITION BY d.COMPANYCODE, d.CRASCODE
            ORDER BY d.publish_date
        ) AS previous_outlook
    FROM subject_daily d
),
subject_events AS (
    SELECT DISTINCT
        DATE_FORMAT(h.publish_date, '%Y-%m') AS ym,
        CAST(h.COMPANYCODE AS CHAR) AS issuer_key,
        CASE
            WHEN h.CROUTLOOK = 1203
             AND h.previous_outlook IS NOT NULL
             AND h.previous_outlook <> 1203
            THEN 'negative_outlook'
            WHEN h.CROUTLOOK = 1208
             AND h.previous_outlook IS NOT NULL
             AND h.previous_outlook <> 1208
            THEN 'downgrade_watch'
        END AS event_type
    FROM subject_history h
    WHERE h.publish_date >= DATE('2024-01-01')
      AND h.publish_date < DATE('2025-01-01')
      AND (
            (h.CROUTLOOK = 1203 AND h.previous_outlook IS NOT NULL AND h.previous_outlook <> 1203)
         OR (h.CROUTLOOK = 1208 AND h.previous_outlook IS NOT NULL AND h.previous_outlook <> 1208)
      )
),
subject_event_month AS (
    SELECT
        ym,
        COUNT(DISTINCT CASE WHEN event_type = 'negative_outlook' THEN issuer_key END) AS negative_outlook_issuer_cnt,
        COUNT(DISTINCT CASE WHEN event_type = 'downgrade_watch' THEN issuer_key END) AS downgrade_watch_issuer_cnt
    FROM subject_events
    GROUP BY ym
)
SELECT
    DATE_FORMAT(m.month_start, '%Y-%m') AS `月份`,
    COALESCE(dm.downgrade_bond_cnt, 0) AS `下调债券只数`,
    COALESCE(dim.downgrade_issuer_cnt, 0) AS `下调债券涉及发行人家数`,
    COALESCE(sem.negative_outlook_issuer_cnt, 0) AS `展望转负面发行人家数`,
    COALESCE(sem.downgrade_watch_issuer_cnt, 0) AS `新列入下调观察发行人家数`
FROM months m
LEFT JOIN downgrade_month dm
  ON dm.ym = DATE_FORMAT(m.month_start, '%Y-%m')
LEFT JOIN downgrade_issuer_month dim
  ON dim.ym = DATE_FORMAT(m.month_start, '%Y-%m')
LEFT JOIN subject_event_month sem
  ON sem.ym = DATE_FORMAT(m.month_start, '%Y-%m')
ORDER BY m.month_start;
