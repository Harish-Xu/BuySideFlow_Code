WITH params AS (
    SELECT DATE('2026-02-13') AS anchor_date
),
target_share AS (
    SELECT
        CASE
            WHEN fa.MAINCODE IS NULL OR CAST(fa.MAINCODE AS CHAR) = '' THEN sm.SECUCODE
            ELSE LPAD(CAST(fa.MAINCODE AS CHAR), 6, '0')
        END AS main_fund_code
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    WHERE sm.SECUCODE = '004002'
    ORDER BY fa.INNERCODE
    LIMIT 1
),
target_fund AS (
    SELECT
        fa.INNERCODE
    FROM target_share ts
    JOIN mf_fundarchives fa
      ON 1 = 1
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    WHERE sm.SECUCODE = ts.main_fund_code
    ORDER BY fa.INNERCODE
    LIMIT 1
),
main_fund_pool AS (
    SELECT
        fa.INNERCODE
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    WHERE CASE
              WHEN fa.MAINCODE IS NULL OR CAST(fa.MAINCODE AS CHAR) = '' THEN sm.SECUCODE
              ELSE LPAD(CAST(fa.MAINCODE AS CHAR), 6, '0')
          END = sm.SECUCODE
),
peer_nav_raw AS (
    SELECT
        n.INNERCODE,
        n.TRADINGDAY,
        n.UNITNVRESTORED AS nav,
        ROW_NUMBER() OVER (
            PARTITION BY n.INNERCODE, n.TRADINGDAY
            ORDER BY n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans n
    JOIN main_fund_pool mp
      ON mp.INNERCODE = n.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE n.TRADINGDAY >= DATE('2023-01-01')
      AND n.TRADINGDAY < DATE('2026-01-01')
      AND n.TRADINGDAY <= p.anchor_date
      AND n.INSERTTIME < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
      AND n.UNITNVRESTORED IS NOT NULL
),
peer_nav_series AS (
    SELECT
        INNERCODE,
        TRADINGDAY,
        nav
    FROM peer_nav_raw
    WHERE rn = 1
),
peer_year_end AS (
    SELECT
        INNERCODE,
        YEAR(TRADINGDAY) AS report_year,
        MAX(TRADINGDAY) AS year_end_nav_date
    FROM peer_nav_series
    WHERE YEAR(TRADINGDAY) BETWEEN 2023 AND 2025
    GROUP BY INNERCODE, YEAR(TRADINGDAY)
),
peer_year_end_nav AS (
    SELECT
        y.INNERCODE,
        y.report_year,
        y.year_end_nav_date,
        s.nav AS year_end_nav
    FROM peer_year_end y
    JOIN peer_nav_series s
      ON s.INNERCODE = y.INNERCODE
     AND s.TRADINGDAY = y.year_end_nav_date
),
peer_annual_base AS (
    SELECT
        INNERCODE,
        report_year,
        year_end_nav_date,
        year_end_nav,
        LAG(report_year) OVER (
            PARTITION BY INNERCODE
            ORDER BY report_year
        ) AS prev_report_year,
        LAG(year_end_nav) OVER (
            PARTITION BY INNERCODE
            ORDER BY report_year
        ) AS prev_year_end_nav
    FROM peer_year_end_nav
),
peer_annual_valid AS (
    SELECT
        INNERCODE,
        report_year,
        year_end_nav_date,
        year_end_nav / NULLIF(prev_year_end_nav, 0) - 1 AS annual_return
    FROM peer_annual_base
    WHERE report_year BETWEEN 2024 AND 2025
      AND prev_report_year = report_year - 1
      AND prev_year_end_nav IS NOT NULL
),
peer_type_asof AS (
    SELECT
        p.INNERCODE,
        p.report_year,
        t.SECASSETCATCODE,
        t.SECASSETCATNAME,
        ROW_NUMBER() OVER (
            PARTITION BY p.INNERCODE, p.report_year
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS rn
    FROM peer_annual_valid p
    JOIN mf_jyfundtype t
      ON t.INNERCODE = p.INNERCODE
    JOIN params prm
      ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= p.year_end_nav_date
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.year_end_nav_date)
      AND t.INSERTTIME < DATE_ADD(prm.anchor_date, INTERVAL 1 DAY)
),
target_nav_raw AS (
    SELECT
        n.TRADINGDAY,
        n.UNITNVRESTORED AS nav,
        ROW_NUMBER() OVER (
            PARTITION BY n.TRADINGDAY
            ORDER BY n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans n
    JOIN target_fund tf
      ON tf.INNERCODE = n.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE n.TRADINGDAY >= DATE('2023-01-01')
      AND n.TRADINGDAY < DATE('2026-01-01')
      AND n.TRADINGDAY <= p.anchor_date
      AND n.INSERTTIME < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
      AND n.UNITNVRESTORED IS NOT NULL
),
target_nav_series AS (
    SELECT
        TRADINGDAY,
        nav
    FROM target_nav_raw
    WHERE rn = 1
),
target_year_end AS (
    SELECT
        YEAR(TRADINGDAY) AS report_year,
        MAX(TRADINGDAY) AS year_end_nav_date
    FROM target_nav_series
    WHERE YEAR(TRADINGDAY) BETWEEN 2023 AND 2025
    GROUP BY YEAR(TRADINGDAY)
),
target_year_end_nav AS (
    SELECT
        y.report_year,
        y.year_end_nav_date,
        s.nav AS year_end_nav
    FROM target_year_end y
    JOIN target_nav_series s
      ON s.TRADINGDAY = y.year_end_nav_date
),
target_annual_base AS (
    SELECT
        report_year,
        year_end_nav_date,
        year_end_nav,
        LAG(report_year) OVER (
            ORDER BY report_year
        ) AS prev_report_year,
        LAG(year_end_nav) OVER (
            ORDER BY report_year
        ) AS prev_year_end_nav
    FROM target_year_end_nav
),
target_annual_return AS (
    SELECT
        report_year,
        year_end_nav_date,
        year_end_nav / NULLIF(prev_year_end_nav, 0) - 1 AS annual_return
    FROM target_annual_base
    WHERE report_year BETWEEN 2024 AND 2025
      AND prev_report_year = report_year - 1
      AND prev_year_end_nav IS NOT NULL
),
target_type_asof AS (
    SELECT
        t.report_year,
        j.SECASSETCATCODE,
        j.SECASSETCATNAME,
        ROW_NUMBER() OVER (
            PARTITION BY t.report_year
            ORDER BY j.EFFECTIVEDATE DESC, j.INSERTTIME DESC, j.ID DESC
        ) AS rn
    FROM target_annual_return t
    JOIN target_fund tf
      ON 1 = 1
    JOIN mf_jyfundtype j
      ON j.INNERCODE = tf.INNERCODE
    JOIN params prm
      ON 1 = 1
    WHERE j.STANDARD = 75
      AND j.EFFECTIVEDATE <= t.year_end_nav_date
      AND (j.CANCELDATE IS NULL OR j.CANCELDATE > t.year_end_nav_date)
      AND j.INSERTTIME < DATE_ADD(prm.anchor_date, INTERVAL 1 DAY)
),
peer_same_cat AS (
    SELECT
        p.report_year,
        p.INNERCODE,
        p.annual_return
    FROM peer_annual_valid p
    JOIN peer_type_asof pt
      ON pt.INNERCODE = p.INNERCODE
     AND pt.report_year = p.report_year
     AND pt.rn = 1
    JOIN target_type_asof tt
      ON tt.report_year = p.report_year
     AND tt.rn = 1
     AND tt.SECASSETCATCODE = pt.SECASSETCATCODE
),
target_rank AS (
    SELECT
        t.report_year,
        t.year_end_nav_date,
        t.annual_return,
        1 + SUM(CASE WHEN p.annual_return > t.annual_return THEN 1 ELSE 0 END) AS annual_return_rank,
        COUNT(p.INNERCODE) AS peer_count
    FROM target_annual_return t
    JOIN target_type_asof tt
      ON tt.report_year = t.report_year
     AND tt.rn = 1
    JOIN peer_same_cat p
      ON p.report_year = t.report_year
    GROUP BY t.report_year, t.year_end_nav_date, t.annual_return
),
target_drawdown_path AS (
    SELECT
        y.report_year,
        s.TRADINGDAY,
        s.nav
    FROM target_year_end_nav y
    JOIN target_nav_series s
      ON YEAR(s.TRADINGDAY) = y.report_year
    WHERE y.report_year BETWEEN 2024 AND 2025

    UNION ALL

    SELECT
        y.report_year,
        p.year_end_nav_date AS TRADINGDAY,
        p.year_end_nav AS nav
    FROM target_year_end_nav y
    JOIN target_year_end_nav p
      ON p.report_year = y.report_year - 1
    WHERE y.report_year BETWEEN 2024 AND 2025
),
target_drawdown AS (
    SELECT
        report_year,
        ABS(MIN(nav / NULLIF(peak_nav, 0) - 1)) AS max_drawdown
    FROM (
        SELECT
            report_year,
            TRADINGDAY,
            nav,
            MAX(nav) OVER (
                PARTITION BY report_year
                ORDER BY TRADINGDAY
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS peak_nav
        FROM target_drawdown_path
    ) d
    GROUP BY report_year
)
SELECT
    tr.report_year,
    DATE(tr.year_end_nav_date) AS year_end_nav_date,
    ROUND(tr.annual_return * 100, 2) AS annual_return_pct,
    CONCAT(tr.annual_return_rank, '/', tr.peer_count) AS peer_rank_over_count,
    ROUND(td.max_drawdown * 100, 2) AS max_drawdown_pct
FROM target_rank tr
LEFT JOIN target_drawdown td
  ON td.report_year = tr.report_year
ORDER BY tr.report_year;
