WITH params AS (
    SELECT DATE('2024-12-31') AS start_2025,
           DATE('2025-12-31') AS end_2025,
           DATE('2026-02-28') AS asof_2026
),
latest_type AS (
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
      AND t.EFFECTIVEDATE <= p.end_2025
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.end_2025)
      AND t.INSERTTIME < DATE_ADD(p.asof_2026, INTERVAL 1 DAY)
),
fund_pool AS (
    SELECT
        fa.INNERCODE,
        sm.SECUCODE,
        sm.SECUABBR
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
    JOIN latest_type lt
      ON lt.INNERCODE = fa.INNERCODE
     AND lt.rn = 1
    JOIN params p
      ON 1 = 1
    WHERE sm.SECUCATEGORY = 8
      AND lt.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
      AND fa.INVESTMENTTYPE = 16
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE < p.start_2025
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > p.end_2025)
),
nav_dedup AS (
    SELECT
        fp.INNERCODE,
        DATE(nv.TRADINGDAY) AS trading_day,
        nv.UNITNVRESTORED,
        ROW_NUMBER() OVER (
            PARTITION BY fp.INNERCODE, DATE(nv.TRADINGDAY)
            ORDER BY nv.TRADINGDAY DESC, nv.INSERTTIME DESC, nv.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans nv
    JOIN fund_pool fp
      ON fp.INNERCODE = nv.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE nv.UNITNVRESTORED IS NOT NULL
      AND nv.INSERTTIME < DATE_ADD(p.asof_2026, INTERVAL 1 DAY)
      AND (
            (nv.TRADINGDAY >= p.start_2025 AND nv.TRADINGDAY < DATE_ADD(p.start_2025, INTERVAL 1 DAY))
         OR (nv.TRADINGDAY >= p.end_2025 AND nv.TRADINGDAY < DATE_ADD(p.end_2025, INTERVAL 1 DAY))
         OR (nv.TRADINGDAY > p.end_2025 AND nv.TRADINGDAY < DATE_ADD(p.asof_2026, INTERVAL 1 DAY))
      )
),
nav_points AS (
    SELECT
        INNERCODE,
        trading_day,
        UNITNVRESTORED
    FROM nav_dedup
    WHERE rn = 1
),
nav_2025 AS (
    SELECT
        INNERCODE,
        MAX(CASE WHEN trading_day = DATE('2024-12-31') THEN UNITNVRESTORED END) AS nav_2024_1231,
        MAX(CASE WHEN trading_day = DATE('2025-12-31') THEN UNITNVRESTORED END) AS nav_2025_1231
    FROM nav_points
    GROUP BY INNERCODE
),
nav_2026_latest AS (
    SELECT
        t.INNERCODE,
        t.UNITNVRESTORED AS nav_2026_latest
    FROM (
        SELECT
            np.INNERCODE,
            np.trading_day,
            np.UNITNVRESTORED,
            ROW_NUMBER() OVER (
                PARTITION BY np.INNERCODE
                ORDER BY np.trading_day DESC
            ) AS rn
        FROM nav_points np
        JOIN params p
          ON 1 = 1
        WHERE np.trading_day > p.end_2025
    ) t
    WHERE t.rn = 1
),
returns_2025 AS (
    SELECT
        fp.INNERCODE,
        fp.SECUCODE,
        fp.SECUABBR,
        (n25.nav_2025_1231 / n25.nav_2024_1231 - 1) AS return_2025
    FROM fund_pool fp
    JOIN nav_2025 n25
      ON n25.INNERCODE = fp.INNERCODE
    WHERE n25.nav_2024_1231 IS NOT NULL
      AND n25.nav_2025_1231 IS NOT NULL
      AND n25.nav_2024_1231 > 0
),
champion_2025 AS (
    SELECT
        r25.*,
        DENSE_RANK() OVER (
            ORDER BY r25.return_2025 DESC
        ) AS rank_2025
    FROM returns_2025 r25
),
returns_2026 AS (
    SELECT
        n25.INNERCODE,
        (n26.nav_2026_latest / n25.nav_2025_1231 - 1) AS return_2026
    FROM nav_2025 n25
    JOIN nav_2026_latest n26
      ON n26.INNERCODE = n25.INNERCODE
    WHERE n25.nav_2025_1231 IS NOT NULL
      AND n25.nav_2025_1231 > 0
),
rank_2026 AS (
    SELECT
        r26.*,
        RANK() OVER (
            ORDER BY r26.return_2026 DESC
        ) AS rank_2026
    FROM returns_2026 r26
)
SELECT
    c25.SECUCODE AS fund_code,
    c25.SECUABBR AS fund_name,
    ROUND(c25.return_2025 * 100, 4) AS return_2025_pct,
    ROUND(r26.return_2026 * 100, 4) AS return_2026_pct,
    r26.rank_2026
FROM champion_2025 c25
LEFT JOIN rank_2026 r26
  ON r26.INNERCODE = c25.INNERCODE
WHERE c25.rank_2025 = 1
ORDER BY c25.SECUCODE ASC;
