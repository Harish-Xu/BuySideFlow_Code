WITH params AS (
    SELECT
        DATE('2025-07-01') AS analysis_start_date,
        DATE('2025-07-31') AS analysis_end_date,
        DATE('2025-06-30') AS initial_date,
        DATE('2025-08-04') AS as_of_date,
        DATE('2025-08-04') AS visible_cutoff
),
target_fund AS (
    SELECT fa.INNERCODE
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
    WHERE sm.SECUCODE = '519702'
    ORDER BY fa.INNERCODE
    LIMIT 1
),
current_managers AS (
    SELECT
        fm.PERSONALCODE,
        fm.NAME AS manager_name,
        DATE(fm.ACCESSIONDATE) AS accession_date
    FROM mf_fundmanagernew fm
    JOIN target_fund tf
      ON tf.INNERCODE = fm.INNERCODE
    CROSS JOIN params p
    WHERE fm.POSTNAME = 1
      AND fm.ACCESSIONDATE <= p.as_of_date
      AND (fm.DIMISSIONDATE IS NULL OR fm.DIMISSIONDATE > p.as_of_date)
      AND fm.INFOPUBLDATE < p.visible_cutoff
),
fund_nav_ranked AS (
    SELECT
        DATE(fn.TRADINGDAY) AS TRADINGDAY,
        fn.UNITNVRESTORED,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(fn.TRADINGDAY)
            ORDER BY fn.INSERTTIME DESC, fn.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans fn
    JOIN target_fund tf
      ON tf.INNERCODE = fn.INNERCODE
    CROSS JOIN params p
    WHERE fn.TRADINGDAY >= p.initial_date
      AND fn.TRADINGDAY < DATE_ADD(p.analysis_end_date, INTERVAL 1 DAY)
      AND fn.INSERTTIME < p.visible_cutoff
),
fund_nav AS (
    SELECT
        DATE(TRADINGDAY) AS trading_day,
        UNITNVRESTORED AS restored_nav
    FROM fund_nav_ranked
    WHERE rn = 1
      AND UNITNVRESTORED IS NOT NULL
      AND UNITNVRESTORED > 0
),
weekly_nav AS (
    SELECT
        YEARWEEK(trading_day, 3) AS iso_year_week,
        MAX(trading_day) AS week_end_date
    FROM fund_nav
    CROSS JOIN params p
    WHERE trading_day >= p.analysis_start_date
      AND trading_day <= p.analysis_end_date
    GROUP BY YEARWEEK(trading_day, 3)
),
weekly_endpoints AS (
    SELECT
        wn.iso_year_week,
        wn.week_end_date,
        fn.restored_nav AS week_end_nav
    FROM weekly_nav wn
    JOIN fund_nav fn
      ON fn.trading_day = wn.week_end_date
),
weekly_intervals AS (
    SELECT
        we.iso_year_week,
        we.week_end_date,
        we.week_end_nav,
        CASE WHEN we.iso_year_week = YEARWEEK(p.analysis_start_date, 3)
             THEN p.initial_date ELSE prev.week_end_date END AS previous_week_end,
        CASE WHEN we.iso_year_week = YEARWEEK(p.analysis_start_date, 3)
             THEN base.restored_nav ELSE prev.week_end_nav END AS previous_week_nav
    FROM weekly_endpoints we
    CROSS JOIN params p
    JOIN fund_nav base ON base.trading_day = p.initial_date
    LEFT JOIN weekly_endpoints prev
      ON prev.iso_year_week = YEARWEEK(DATE_SUB(we.week_end_date, INTERVAL 7 DAY), 3)
),
benchmark_ranked AS (
    SELECT
        DATE(bg.ENDDATE) AS ENDDATE,
        bg.DAILYBENCHGR,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(bg.ENDDATE)
            ORDER BY bg.INSERTTIME DESC, bg.ID DESC
        ) AS rn
    FROM mf_benchmarkgrowthrate bg
    JOIN target_fund tf
      ON tf.INNERCODE = bg.INNERCODE
    CROSS JOIN params p
    WHERE bg.ENDDATE > p.initial_date
      AND bg.ENDDATE < DATE_ADD(p.analysis_end_date, INTERVAL 1 DAY)
      AND bg.INSERTTIME < p.visible_cutoff
),
benchmark_daily AS (
    SELECT
        DATE(ENDDATE) AS benchmark_date,
        DAILYBENCHGR
    FROM benchmark_ranked
    WHERE rn = 1
),
interval_returns AS (
    SELECT
        wi.iso_year_week,
        wi.previous_week_end,
        wi.week_end_date,
        wi.week_end_nav / wi.previous_week_nav - 1 AS fund_weekly_return,
        EXP(SUM(LN(1 + bd.DAILYBENCHGR / 100.0))) - 1 AS benchmark_weekly_return,
        COUNT(bd.benchmark_date) AS benchmark_observations
    FROM weekly_intervals wi
    LEFT JOIN benchmark_daily bd
      ON bd.benchmark_date > wi.previous_week_end
     AND bd.benchmark_date <= wi.week_end_date
    WHERE wi.previous_week_end IS NOT NULL
      AND wi.previous_week_nav > 0
    GROUP BY
        wi.iso_year_week,
        wi.previous_week_end,
        wi.week_end_date,
        wi.week_end_nav,
        wi.previous_week_nav
    HAVING COUNT(bd.benchmark_date) > 0
       AND COUNT(bd.benchmark_date) = COUNT(CASE WHEN bd.DAILYBENCHGR IS NOT NULL AND bd.DAILYBENCHGR > -100 THEN 1 END)
),
manager_weeks AS (
    SELECT
        cm.PERSONALCODE,
        cm.manager_name,
        cm.accession_date,
        ir.iso_year_week,
        CASE
            WHEN ir.fund_weekly_return > ir.benchmark_weekly_return THEN 1
            ELSE 0
        END AS is_win
    FROM current_managers cm
    CROSS JOIN interval_returns ir
)
SELECT
    manager_name AS `基金经理姓名`,
    accession_date AS `任职日期`,
    ROUND(AVG(is_win), 4) AS `周胜率`,
    COUNT(*) AS `样本周数`
FROM manager_weeks
GROUP BY
    PERSONALCODE,
    manager_name,
    accession_date
ORDER BY accession_date;
