WITH params AS (
    SELECT
        DATE('2025-12-31') AS ytd_base_day,
        DATE('2022-01-01') AS manager_stable_start,
        DATE('2026-02-28') AS as_of_day
),
type_asof AS (
    SELECT
        t.INNERCODE,
        t.THIRDASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS rn
    FROM mf_jyfundtype t
    JOIN params p ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= p.as_of_day
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.as_of_day)
      AND t.INSERTTIME < DATE_ADD(p.as_of_day, INTERVAL 1 DAY)
),
peer_funds AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        sm.SECUABBR AS fund_name,
        fa.ESTABLISHMENTDATE
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
    JOIN type_asof ta
      ON ta.INNERCODE = fa.INNERCODE
     AND ta.rn = 1
    JOIN params p ON 1 = 1
    WHERE ta.THIRDASSETCATCODE = 120401
      AND sm.SECUCATEGORY = 8
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.as_of_day)
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.TYPE IN (2, 3, 4, 7, 8)
      AND COALESCE(fa.IFFOF, 2) <> 1
),
base_funds AS (
    SELECT *
    FROM peer_funds
    WHERE ESTABLISHMENTDATE < DATE('2022-01-01')
),
nav_visible AS (
    SELECT
        n.INNERCODE,
        DATE(n.TRADINGDAY) AS trading_day,
        n.UNITNVRESTORED,
        ROW_NUMBER() OVER (
            PARTITION BY n.INNERCODE, DATE(n.TRADINGDAY)
            ORDER BY n.UPDATETIME DESC, n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans n
    JOIN base_funds bf
      ON bf.INNERCODE = n.INNERCODE
    JOIN params p ON 1 = 1
    WHERE n.UNITNVRESTORED IS NOT NULL
      AND n.TRADINGDAY <= p.as_of_day
      AND n.INSERTTIME < DATE_ADD(p.as_of_day, INTERVAL 1 DAY)
),
annual_boundaries AS (
    SELECT 2022 AS yr, DATE('2021-12-31') AS start_day, DATE('2022-12-31') AS end_day
    UNION ALL SELECT 2023, DATE('2022-12-31'), DATE('2023-12-31')
    UNION ALL SELECT 2024, DATE('2023-12-31'), DATE('2024-12-31')
    UNION ALL SELECT 2025, DATE('2024-12-31'), DATE('2025-12-31')
),
annual_nav_ranked AS (
    SELECT
        bf.INNERCODE,
        ab.yr,
        'start' AS point_type,
        nv.trading_day,
        nv.UNITNVRESTORED,
        ROW_NUMBER() OVER (
            PARTITION BY bf.INNERCODE, ab.yr, 'start'
            ORDER BY nv.trading_day DESC
        ) AS point_rn
    FROM base_funds bf
    CROSS JOIN annual_boundaries ab
    JOIN nav_visible nv
      ON nv.INNERCODE = bf.INNERCODE
     AND nv.rn = 1
     AND nv.trading_day <= ab.start_day
    UNION ALL
    SELECT
        bf.INNERCODE,
        ab.yr,
        'end' AS point_type,
        nv.trading_day,
        nv.UNITNVRESTORED,
        ROW_NUMBER() OVER (
            PARTITION BY bf.INNERCODE, ab.yr, 'end'
            ORDER BY nv.trading_day DESC
        ) AS point_rn
    FROM base_funds bf
    CROSS JOIN annual_boundaries ab
    JOIN nav_visible nv
      ON nv.INNERCODE = bf.INNERCODE
     AND nv.rn = 1
     AND nv.trading_day <= ab.end_day
),
year_returns AS (
    SELECT
        anr.INNERCODE,
        anr.yr,
        MAX(CASE WHEN anr.point_type = 'end' THEN anr.UNITNVRESTORED END)
        / NULLIF(MAX(CASE WHEN anr.point_type = 'start' THEN anr.UNITNVRESTORED END), 0) - 1
        AS annual_nav_growth_rate
    FROM annual_nav_ranked anr
    WHERE anr.point_rn = 1
    GROUP BY anr.INNERCODE, anr.yr
),
all_positive AS (
    SELECT yr.INNERCODE
    FROM year_returns yr
    WHERE yr.yr BETWEEN 2022 AND 2025
    GROUP BY yr.INNERCODE
    HAVING COUNT(DISTINCT yr.yr) = 4
       AND COUNT(yr.annual_nav_growth_rate) = 4
       AND MIN(yr.annual_nav_growth_rate) > 0
),
no_mgr_change AS (
    SELECT ap.INNERCODE
    FROM all_positive ap
    JOIN params p ON 1 = 1
    WHERE NOT EXISTS (
        SELECT 1
        FROM mf_fundmanagernew m
        WHERE m.INNERCODE = ap.INNERCODE
          AND m.POSTNAME = 1
          AND (m.INFOPUBLDATE IS NULL OR m.INFOPUBLDATE < DATE_ADD(p.as_of_day, INTERVAL 1 DAY))
          AND (
              (m.ACCESSIONDATE IS NOT NULL AND m.ACCESSIONDATE >= p.manager_stable_start AND m.ACCESSIONDATE <= p.as_of_day)
              OR (m.DIMISSIONDATE IS NOT NULL AND m.DIMISSIONDATE >= p.manager_stable_start AND m.DIMISSIONDATE <= p.as_of_day)
          )
    )
),
current_manager_pairs AS (
    SELECT DISTINCT
        m.INNERCODE,
        m.PERSONALCODE,
        m.NAME,
        m.ACCESSIONDATE
    FROM mf_fundmanagernew m
    JOIN params p ON 1 = 1
    WHERE m.POSTNAME = 1
      AND (m.INFOPUBLDATE IS NULL OR m.INFOPUBLDATE < DATE_ADD(p.as_of_day, INTERVAL 1 DAY))
      AND (m.ACCESSIONDATE IS NULL OR m.ACCESSIONDATE <= p.as_of_day)
      AND (m.DIMISSIONDATE IS NULL OR m.DIMISSIONDATE > p.as_of_day)
),
manager_ranked AS (
    SELECT
        nmc.INNERCODE,
        cmp.PERSONALCODE,
        ROW_NUMBER() OVER (
            PARTITION BY cmp.PERSONALCODE
            ORDER BY bf.ESTABLISHMENTDATE ASC, bf.fund_code ASC
        ) AS manager_rank
    FROM no_mgr_change nmc
    JOIN current_manager_pairs cmp
      ON cmp.INNERCODE = nmc.INNERCODE
    JOIN base_funds bf
      ON bf.INNERCODE = nmc.INNERCODE
),
kept_funds AS (
    SELECT mr.INNERCODE
    FROM manager_ranked mr
    GROUP BY mr.INNERCODE
    HAVING MAX(mr.manager_rank) = 1
),
current_manager_list AS (
    SELECT
        cmp.INNERCODE,
        GROUP_CONCAT(DISTINCT cmp.NAME ORDER BY cmp.ACCESSIONDATE, cmp.PERSONALCODE SEPARATOR ',') AS current_manager
    FROM current_manager_pairs cmp
    GROUP BY cmp.INNERCODE
),
peer_nav_anchors AS (
    SELECT
        pf.INNERCODE,
        (
            SELECT n.UNITNVRESTORED
            FROM mf_fundnetvalueretrans n
            JOIN params p ON 1 = 1
            WHERE n.INNERCODE = pf.INNERCODE
              AND n.UNITNVRESTORED IS NOT NULL
              AND n.TRADINGDAY <= p.ytd_base_day
              AND n.INSERTTIME < DATE_ADD(p.as_of_day, INTERVAL 1 DAY)
            ORDER BY n.TRADINGDAY DESC, n.UPDATETIME DESC, n.INSERTTIME DESC, n.ID DESC
            LIMIT 1
        ) AS nav_2025_anchor,
        (
            SELECT n.UNITNVRESTORED
            FROM mf_fundnetvalueretrans n
            JOIN params p ON 1 = 1
            WHERE n.INNERCODE = pf.INNERCODE
              AND n.UNITNVRESTORED IS NOT NULL
              AND n.TRADINGDAY <= p.as_of_day
              AND n.INSERTTIME < DATE_ADD(p.as_of_day, INTERVAL 1 DAY)
            ORDER BY n.TRADINGDAY DESC, n.UPDATETIME DESC, n.INSERTTIME DESC, n.ID DESC
            LIMIT 1
        ) AS nav_2026_latest
    FROM peer_funds pf
),
peer_returns_2026 AS (
    SELECT
        pf.INNERCODE,
        pna.nav_2026_latest / NULLIF(pna.nav_2025_anchor, 0) - 1 AS return_2026
    FROM peer_funds pf
    JOIN peer_nav_anchors pna
      ON pna.INNERCODE = pf.INNERCODE
    WHERE pna.nav_2025_anchor IS NOT NULL
      AND pna.nav_2025_anchor > 0
      AND pna.nav_2026_latest IS NOT NULL
),
peer_rank_2026 AS (
    SELECT
        pr.INNERCODE,
        pr.return_2026,
        RANK() OVER (
            ORDER BY pr.return_2026 DESC
        ) AS peer_rank_2026
    FROM peer_returns_2026 pr
)
SELECT
    bf.fund_code AS `基金代码`,
    bf.fund_name AS `基金简称`,
    DATE_FORMAT(bf.ESTABLISHMENTDATE, '%Y-%m-%d') AS `成立日期`,
    ROUND(pr26.return_2026 * 100, 2) AS `2026年以来收益率`,
    pr26.peer_rank_2026 AS `同类排名`,
    cml.current_manager AS `在任基金经理`
FROM kept_funds kf
JOIN base_funds bf
  ON bf.INNERCODE = kf.INNERCODE
JOIN peer_rank_2026 pr26
  ON pr26.INNERCODE = kf.INNERCODE
JOIN current_manager_list cml
  ON cml.INNERCODE = kf.INNERCODE
ORDER BY pr26.return_2026 DESC, bf.fund_code ASC;
