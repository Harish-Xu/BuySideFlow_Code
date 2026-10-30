WITH year_params AS (
    SELECT 2023 AS y, DATE('2022-12-31') AS prev_year_end, DATE('2023-12-31') AS year_end
    UNION ALL
    SELECT 2024 AS y, DATE('2023-12-31') AS prev_year_end, DATE('2024-12-31') AS year_end
    UNION ALL
    SELECT 2025 AS y, DATE('2024-12-31') AS prev_year_end, DATE('2025-12-31') AS year_end
),
type_asof AS (
    SELECT
        yp.y,
        t.INNERCODE,
        t.SECASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY yp.y, t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS rn
    FROM year_params yp
    JOIN mf_jyfundtype t
      ON t.STANDARD = 75
     AND t.EFFECTIVEDATE <= yp.year_end
     AND (t.CANCELDATE IS NULL OR t.CANCELDATE > yp.year_end)
     AND t.INSERTTIME < DATE_ADD(yp.year_end, INTERVAL 1 DAY)
),
peer_funds AS (
    SELECT
        yp.y,
        f.INNERCODE,
        sm.SECUCODE,
        sm.SECUABBR,
        yp.prev_year_end,
        yp.year_end
    FROM year_params yp
    JOIN type_asof ta
      ON ta.y = yp.y
     AND ta.rn = 1
     AND ta.SECASSETCATCODE = 1201
    JOIN mf_fundarchives f
      ON f.INNERCODE = ta.INNERCODE
    JOIN secumain sm
      ON sm.INNERCODE = f.INNERCODE
    WHERE COALESCE(f.IFFOF, 2) <> 1
      AND f.MAINCODE = f.SECURITYCODE
      AND f.ESTABLISHMENTDATE <= yp.year_end
      AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE > yp.year_end)
),
candidate_funds AS (
    SELECT DISTINCT
        INNERCODE
    FROM peer_funds
),
boundary_params AS (
    SELECT 2022 AS boundary_y, DATE('2022-12-31') AS boundary_date
    UNION ALL
    SELECT 2023 AS boundary_y, DATE('2023-12-31') AS boundary_date
    UNION ALL
    SELECT 2024 AS boundary_y, DATE('2024-12-31') AS boundary_date
    UNION ALL
    SELECT 2025 AS boundary_y, DATE('2025-12-31') AS boundary_date
),
boundary_nav_ranked AS (
    SELECT
        n.INNERCODE,
        bp.boundary_y,
        DATE(n.TRADINGDAY) AS trading_day,
        n.UNITNVRESTORED,
        ROW_NUMBER() OVER (
            PARTITION BY n.INNERCODE, bp.boundary_y
            ORDER BY n.TRADINGDAY DESC, n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans n
    JOIN candidate_funds c
      ON c.INNERCODE = n.INNERCODE
    JOIN boundary_params bp
      ON n.TRADINGDAY <= bp.boundary_date
     AND n.TRADINGDAY >= DATE_FORMAT(bp.boundary_date, '%Y-%m-01')
    WHERE n.UNITNVRESTORED IS NOT NULL
      AND n.TRADINGDAY <= DATE('2025-12-31')
      AND n.UNITNVRESTORED > 0
),
boundary_nav AS (
    SELECT
        bn.INNERCODE,
        bn.boundary_y,
        bn.trading_day,
        bn.UNITNVRESTORED
    FROM boundary_nav_ranked bn
    WHERE bn.rn = 1
),
year_ret AS (
    SELECT
        pf.y,
        pf.INNERCODE,
        pf.SECUCODE,
        pf.SECUABBR,
        bn_end.UNITNVRESTORED / NULLIF(bn_start.UNITNVRESTORED, 0) - 1 AS annual_ret
    FROM peer_funds pf
    JOIN boundary_nav bn_end
      ON bn_end.INNERCODE = pf.INNERCODE
     AND bn_end.boundary_y = pf.y
    JOIN boundary_nav bn_start
      ON bn_start.INNERCODE = pf.INNERCODE
     AND bn_start.boundary_y = pf.y - 1
),
ranked AS (
    SELECT
        yr.*,
        RANK() OVER (PARTITION BY yr.y ORDER BY yr.annual_ret DESC) AS rank_pos,
        COUNT(*) OVER (PARTITION BY yr.y) AS sample_cnt
    FROM year_ret yr
    WHERE yr.annual_ret IS NOT NULL
),
qualified AS (
    SELECT
        r.INNERCODE
    FROM ranked r
    JOIN mf_fundarchives f
      ON f.INNERCODE = r.INNERCODE
    WHERE f.ESTABLISHMENTDATE < DATE('2023-01-01')
    GROUP BY r.INNERCODE
    HAVING COUNT(*) = 3
       AND SUM(CASE WHEN r.rank_pos <= CEIL(r.sample_cnt * 0.1) THEN 1 ELSE 0 END) = 3
)
SELECT
    r.SECUCODE AS `基金代码`,
    r.SECUABBR AS `基金简称`,
    MAX(CASE WHEN r.y = 2023 THEN ROUND(r.annual_ret * 100, 2) END) AS `2023年收益率`,
    MAX(CASE WHEN r.y = 2023 THEN r.rank_pos END) AS `2023年排名`,
    MAX(CASE WHEN r.y = 2023 THEN r.sample_cnt END) AS `2023年同类样本数`,
    MAX(CASE WHEN r.y = 2024 THEN ROUND(r.annual_ret * 100, 2) END) AS `2024年收益率`,
    MAX(CASE WHEN r.y = 2024 THEN r.rank_pos END) AS `2024年排名`,
    MAX(CASE WHEN r.y = 2024 THEN r.sample_cnt END) AS `2024年同类样本数`,
    MAX(CASE WHEN r.y = 2025 THEN ROUND(r.annual_ret * 100, 2) END) AS `2025年收益率`,
    MAX(CASE WHEN r.y = 2025 THEN r.rank_pos END) AS `2025年排名`,
    MAX(CASE WHEN r.y = 2025 THEN r.sample_cnt END) AS `2025年同类样本数`
FROM ranked r
JOIN qualified q
  ON q.INNERCODE = r.INNERCODE
GROUP BY r.INNERCODE, r.SECUCODE, r.SECUABBR
ORDER BY
    MAX(CASE WHEN r.y = 2025 THEN r.rank_pos END),
    MAX(CASE WHEN r.y = 2024 THEN r.rank_pos END),
    MAX(CASE WHEN r.y = 2023 THEN r.rank_pos END),
    r.SECUCODE;
