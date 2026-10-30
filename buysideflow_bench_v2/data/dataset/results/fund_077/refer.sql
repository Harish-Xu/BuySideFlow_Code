WITH params AS (
    SELECT
        DATE('2022-12-31') AS prev_year_end,
        DATE('2023-01-01') AS year_start,
        DATE('2023-12-31') AS year_end,
        TIMESTAMP('2024-01-01 00:00:00') AS visible_cutoff
),
type_asof AS (
    SELECT
        t.INNERCODE,
        t.SECASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS rn
    FROM mf_jyfundtype t
    JOIN params p
      ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= p.year_end
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.year_end)
      AND t.INSERTTIME < p.visible_cutoff
),
quant_main_funds AS (
    SELECT
        f.INNERCODE,
        s.SECUCODE,
        s.SECUABBR
    FROM mf_fundarchives f
    JOIN secumain s
      ON s.INNERCODE = f.INNERCODE
    JOIN type_asof ta
      ON ta.INNERCODE = f.INNERCODE
     AND ta.rn = 1
    JOIN params p
      ON 1 = 1
    WHERE f.ESTABLISHMENTDATE < p.year_start
      AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE > p.year_end)
      AND f.TYPE IN (2, 3, 4, 7, 8)
      AND COALESCE(f.IFFOF, 2) <> 1
      AND f.MAINCODE = f.SECURITYCODE
      AND ta.SECASSETCATCODE = 1101
      AND (f.INSERTTIME IS NULL OR f.INSERTTIME < p.visible_cutoff)
      AND (
            COALESCE(s.SECUABBR, '') LIKE '%量化%'
         OR COALESCE(s.CHINAMEABBR, '') LIKE '%量化%'
         OR COALESCE(f.INVESTORIENTATION, '') LIKE '%量化%'
      )
),
turnover_asof_ranked AS (
    SELECT
        t.INNERCODE,
        t.TURNOVERRATEONEY,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY
                t.REPORTDATE DESC,
                COALESCE(t.INFOPUBLDATE, t.REPORTDATE) DESC,
                t.INSERTTIME DESC,
                t.ID DESC
        ) AS rn
    FROM mf_fundtradeinfo t
    JOIN quant_main_funds q
      ON q.INNERCODE = t.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE t.TURNOVERRATEONEY IS NOT NULL
      AND t.REPORTDATE <= p.year_end
      AND t.INFOPUBLDATE < p.visible_cutoff
),
nav_day_dedup AS (
    SELECT
        n.INNERCODE,
        DATE(n.TRADINGDAY) AS trading_day,
        n.UNITNVRESTORED,
        ROW_NUMBER() OVER (
            PARTITION BY n.INNERCODE, DATE(n.TRADINGDAY)
            ORDER BY n.TRADINGDAY DESC, n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans n
    JOIN quant_main_funds q
      ON q.INNERCODE = n.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE n.UNITNVRESTORED IS NOT NULL
      AND n.TRADINGDAY <= p.year_end
      AND n.INSERTTIME < p.visible_cutoff
),
nav_base AS (
    SELECT
        nd.INNERCODE,
        nd.trading_day,
        nd.UNITNVRESTORED
    FROM nav_day_dedup nd
    WHERE nd.rn = 1
),
start_nav AS (
    SELECT
        x.INNERCODE,
        x.UNITNVRESTORED AS start_nv
    FROM (
        SELECT
            nb.INNERCODE,
            nb.UNITNVRESTORED,
            ROW_NUMBER() OVER (
                PARTITION BY nb.INNERCODE
                ORDER BY nb.trading_day DESC
            ) AS rn
        FROM nav_base nb
        JOIN params p
          ON 1 = 1
        WHERE nb.trading_day <= p.prev_year_end
    ) x
    WHERE x.rn = 1
),
end_nav AS (
    SELECT
        x.INNERCODE,
        x.UNITNVRESTORED AS end_nv
    FROM (
        SELECT
            nb.INNERCODE,
            nb.UNITNVRESTORED,
            ROW_NUMBER() OVER (
                PARTITION BY nb.INNERCODE
                ORDER BY nb.trading_day DESC
            ) AS rn
        FROM nav_base nb
        JOIN params p
          ON 1 = 1
        WHERE nb.trading_day <= p.year_end
    ) x
    WHERE x.rn = 1
),
return_2023 AS (
    SELECT
        s.INNERCODE,
        s.start_nv,
        e.end_nv
    FROM start_nav s
    JOIN end_nav e
      ON e.INNERCODE = s.INNERCODE
)
SELECT
    q.SECUCODE AS `基金代码`,
    q.SECUABBR AS `基金简称`,
    ROUND((r.end_nv / NULLIF(r.start_nv, 0) - 1) * 100, 2) AS `2023年收益率`,
    ROUND(t.TURNOVERRATEONEY * 100, 2) AS `换手率`
FROM quant_main_funds q
JOIN return_2023 r
  ON r.INNERCODE = q.INNERCODE
JOIN turnover_asof_ranked t
  ON t.INNERCODE = q.INNERCODE
 AND t.rn = 1
WHERE r.end_nv / NULLIF(r.start_nv, 0) - 1 > 0
  AND t.TURNOVERRATEONEY <= 2.0
ORDER BY
    t.TURNOVERRATEONEY ASC,
    (r.end_nv / NULLIF(r.start_nv, 0) - 1) DESC,
    q.SECUCODE
