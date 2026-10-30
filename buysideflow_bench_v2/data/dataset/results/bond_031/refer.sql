WITH
basic_issuer AS (
    SELECT
        MAINCODE,
        MAX(ISSUER) AS ISSUER
    FROM bond_basicinfo
    WHERE ISSUER IS NOT NULL
      AND ISSUER <> ''
    GROUP BY MAINCODE
),
actual_default_events AS (
    SELECT
        bc.MAINCODE,
        bc.SECUCODE,
        bc.SECUABBR,
        COALESCE(NULLIF(bc.ISSUER, ''), bi.ISSUER) AS ISSUER,
        d.DEFAULTDATE,
        d.INFOPUBLDATE AS default_publish_date,
        d.ID
    FROM bond_default d
    JOIN bond_code bc ON bc.INNERCODE = d.INNERCODE
    LEFT JOIN basic_issuer bi ON bi.MAINCODE = bc.MAINCODE
    WHERE d.DEFAULTDATE IS NOT NULL
      AND d.INFOPUBLDATE < DATE('2026-01-16')
      -- 201/202/203 are actual interest/principal/default events; 801 is other default.
      -- Expected defaults, revocations and non-default records are not first-default events.
      AND d.DEFAULTPROG IN (201, 202, 203, 801)
),
ranked_first_defaults AS (
    SELECT
        MAINCODE,
        SECUCODE,
        SECUABBR,
        ISSUER,
        DEFAULTDATE,
        ROW_NUMBER() OVER (
            PARTITION BY MAINCODE
            ORDER BY DEFAULTDATE ASC, default_publish_date ASC, ID ASC, SECUCODE ASC
        ) AS rn
    FROM actual_default_events
),
first_defaults_2025 AS (
    SELECT
        MAINCODE,
        SECUCODE,
        SECUABBR,
        ISSUER,
        DATE(DEFAULTDATE) AS first_default_date
    FROM ranked_first_defaults
    WHERE rn = 1
      AND DATE(DEFAULTDATE) >= '2025-01-01'
      AND DATE(DEFAULTDATE) < '2026-01-01'
),
downgrade_events AS (
    SELECT
        fd.MAINCODE,
        g.ID AS rating_id,
        g.CRASNAME,
        DATE(g.INFOPUBLDATE) AS downgrade_date,
        g.LASTCRDESC,
        g.CRDESC
    FROM first_defaults_2025 fd
    JOIN bond_bdcreditgrading g ON g.MAINCODE = fd.MAINCODE
    WHERE g.INFOPUBLDATE IS NOT NULL
      AND g.CRASNAME IS NOT NULL
      AND g.CRTYPECODE = 2
      AND g.CRSTATUS = 1
      AND g.CRCHANGE = '调低'
      AND DATE(g.INFOPUBLDATE) >= DATE_SUB(fd.first_default_date, INTERVAL 90 DAY)
      AND DATE(g.INFOPUBLDATE) < fd.first_default_date
)
SELECT
    fd.SECUCODE AS `债券代码`,
    fd.SECUABBR AS `债券简称`,
    fd.ISSUER AS `发行人`,
    DATE_FORMAT(fd.first_default_date, '%Y-%m-%d') AS `首次违约日`,
    CASE WHEN de.MAINCODE IS NULL THEN '否' ELSE '是' END AS `是否有下调`,
    de.CRASNAME AS `评级机构`,
    DATE_FORMAT(de.downgrade_date, '%Y-%m-%d') AS `下调公告日`,
    de.LASTCRDESC AS `调低前评级`,
    de.CRDESC AS `调低后评级`,
    DATEDIFF(fd.first_default_date, de.downgrade_date) AS `距违约天数`
FROM first_defaults_2025 fd
LEFT JOIN downgrade_events de ON de.MAINCODE = fd.MAINCODE
ORDER BY
    fd.first_default_date ASC,
    fd.SECUCODE ASC,
    de.downgrade_date ASC,
    de.rating_id ASC;
