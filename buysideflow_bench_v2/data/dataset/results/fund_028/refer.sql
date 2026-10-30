WITH params AS (
    SELECT
        DATE('2025-10-10') AS anchor_date,
        DATE('2024-12-31') AS prior_year_end
),
target_fund AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        sm.SECUABBR AS fund_name,
        DATE(fa.ESTABLISHMENTDATE) AS establishment_date,
        fa.PERFORMANCEBENCHMARK AS performance_benchmark
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    WHERE fa.SECURITYCODE = '001734'
    ORDER BY fa.INNERCODE
    LIMIT 1
),
type_asof AS (
    SELECT
        t.INNERCODE,
        t.FIRSTASSETCATNAME,
        t.SECASSETCATNAME,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS rn
    FROM mf_jyfundtype t
    JOIN target_fund f
      ON f.INNERCODE = t.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= p.anchor_date
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.anchor_date)
      AND t.INSERTTIME < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
),
manager_asof AS (
    SELECT
        m.INNERCODE,
        m.PERSONALCODE,
        m.NAME AS manager_name,
        DATE(m.ACCESSIONDATE) AS accession_date,
        ROW_NUMBER() OVER (
            PARTITION BY m.INNERCODE, m.PERSONALCODE
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC, m.ID DESC
        ) AS rn
    FROM mf_fundmanagernew m
    JOIN target_fund f
      ON f.INNERCODE = m.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE m.POSTNAME = 1
      AND m.ACCESSIONDATE <= p.anchor_date
      AND (m.DIMISSIONDATE IS NULL OR m.DIMISSIONDATE > p.anchor_date)
      AND m.INFOPUBLDATE < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
),
manager_list AS (
    SELECT
        INNERCODE,
        GROUP_CONCAT(
            manager_name
            ORDER BY accession_date, PERSONALCODE
            SEPARATOR ','
        ) AS current_manager
    FROM manager_asof
    WHERE rn = 1
    GROUP BY INNERCODE
),
nav_ranked AS (
    SELECT
        DATE(n.TRADINGDAY) AS trading_day,
        n.UNITNVRESTORED AS nav,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(n.TRADINGDAY)
            ORDER BY n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans n
    JOIN target_fund f
      ON f.INNERCODE = n.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE n.TRADINGDAY < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
      AND n.INSERTTIME < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
      AND n.UNITNVRESTORED > 0
),
nav_series AS (
    SELECT trading_day, nav
    FROM nav_ranked
    WHERE rn = 1
),
nav_bounds AS (
    SELECT
        MAX(CASE WHEN n.trading_day <= p.prior_year_end THEN n.trading_day END) AS start_date,
        MAX(CASE WHEN n.trading_day <= p.anchor_date THEN n.trading_day END) AS end_date
    FROM nav_series n
    JOIN params p
      ON 1 = 1
),
nav_window AS (
    SELECT
        n.trading_day,
        n.nav
    FROM nav_series n
    JOIN nav_bounds b
      ON n.trading_day BETWEEN b.start_date AND b.end_date
),
nav_path AS (
    SELECT
        trading_day,
        nav,
        MAX(nav) OVER (
            ORDER BY trading_day
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS peak_nav
    FROM nav_window
),
nav_metrics AS (
    SELECT
        b.start_date,
        b.end_date,
        ns.nav AS start_nav,
        ne.nav AS end_nav,
        MIN(p.nav / NULLIF(p.peak_nav, 0) - 1) AS max_drawdown
    FROM nav_bounds b
    JOIN nav_series ns
      ON ns.trading_day = b.start_date
    JOIN nav_series ne
      ON ne.trading_day = b.end_date
    JOIN nav_path p
      ON 1 = 1
    GROUP BY b.start_date, b.end_date, ns.nav, ne.nav
)
SELECT
    f.fund_code AS `基金代码`,
    f.fund_name AS `基金简称`,
    f.establishment_date AS `成立日期`,
    t.FIRSTASSETCATNAME AS `聚源一级分类`,
    t.SECASSETCATNAME AS `聚源二级分类`,
    m.current_manager AS `现任基金经理`,
    f.performance_benchmark AS `业绩比较基准`,
    nm.end_date AS `净值统计截至日`,
    ROUND(nm.end_nav / NULLIF(nm.start_nav, 0) - 1, 6) AS `今年以来累计收益率`,
    ROUND(nm.max_drawdown, 6) AS `最大回撤`
FROM target_fund f
LEFT JOIN type_asof t
  ON t.INNERCODE = f.INNERCODE
 AND t.rn = 1
LEFT JOIN manager_list m
  ON m.INNERCODE = f.INNERCODE
JOIN nav_metrics nm
  ON 1 = 1;
