WITH params AS (
    SELECT DATE('2025-09-05') AS as_of_date
),
fund AS (
    SELECT sm.INNERCODE
    FROM secumain sm
    WHERE sm.SECUCODE IN ('001306.OF', '001306')
    ORDER BY CASE WHEN sm.SECUCODE = '001306.OF' THEN 0 ELSE 1 END
    LIMIT 1
),
manager_onboard_events AS (
    SELECT
        fm.INNERCODE,
        DATE(fm.ACCESSIONDATE) AS onboarding_date,
        GROUP_CONCAT(DISTINCT fm.NAME ORDER BY fm.NAME SEPARATOR ', ') AS new_managers
    FROM mf_fundmanagernew fm
    JOIN fund f
      ON f.INNERCODE = fm.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE fm.ACCESSIONDATE IS NOT NULL
      AND fm.POSTNAME = 1
      AND DATE(fm.ACCESSIONDATE) <= p.as_of_date
      AND fm.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND EXISTS (
          SELECT 1
          FROM mf_fundmanagernew prev
          WHERE prev.INNERCODE = fm.INNERCODE
            AND prev.POSTNAME = 1
            AND prev.ACCESSIONDATE IS NOT NULL
            AND DATE(prev.ACCESSIONDATE) < DATE(fm.ACCESSIONDATE)
            AND prev.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      )
    GROUP BY fm.INNERCODE, DATE(fm.ACCESSIONDATE)
),
events AS (
    SELECT
        moe.INNERCODE,
        moe.onboarding_date,
        moe.new_managers,
        ROW_NUMBER() OVER (ORDER BY moe.onboarding_date, moe.new_managers) AS change_seq
    FROM manager_onboard_events moe
),
nav_dedup AS (
    SELECT
        nv.INNERCODE,
        DATE(nv.TRADINGDAY) AS trade_date,
        nv.NVRDAILYGROWTHRATE,
        ROW_NUMBER() OVER (
            PARTITION BY nv.INNERCODE, DATE(nv.TRADINGDAY)
            ORDER BY nv.TRADINGDAY DESC, nv.UPDATETIME DESC, nv.INSERTTIME DESC, nv.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans nv
    JOIN fund f
      ON f.INNERCODE = nv.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE nv.TRADINGDAY < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND nv.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND nv.UPDATETIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
),
bench_dedup AS (
    SELECT
        b.INNERCODE,
        DATE(b.ENDDATE) AS trade_date,
        b.DAILYBENCHGR,
        ROW_NUMBER() OVER (
            PARTITION BY b.INNERCODE, DATE(b.ENDDATE)
            ORDER BY b.ENDDATE DESC, b.UPDATATIME DESC, b.INSERTTIME DESC, b.JSID DESC
        ) AS rn
    FROM mf_benchmarkgrowthrate b
    JOIN fund f
      ON f.INNERCODE = b.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE b.ENDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND b.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND b.UPDATATIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
),
daily_returns AS (
    SELECT
        nd.INNERCODE,
        nd.trade_date,
        nd.NVRDAILYGROWTHRATE / 100.0 AS fund_ret,
        bd.DAILYBENCHGR / 100.0 AS bench_ret
    FROM nav_dedup nd
    JOIN bench_dedup bd
      ON bd.INNERCODE = nd.INNERCODE
     AND bd.trade_date = nd.trade_date
    WHERE nd.rn = 1
      AND bd.rn = 1
      AND nd.NVRDAILYGROWTHRATE IS NOT NULL
      AND bd.DAILYBENCHGR IS NOT NULL
),
trade_calendar AS (
    SELECT DISTINCT DATE(td.TRADINGDATE) AS trade_date
    FROM qt_tradingdaynew td
    JOIN params p
      ON DATE(td.TRADINGDATE) <= p.as_of_date
    WHERE td.SECUMARKET = 83
      AND td.IFTRADINGDAY = 1
),
before_calendar_ranked AS (
    SELECT
        e.change_seq,
        tc.trade_date,
        ROW_NUMBER() OVER (
            PARTITION BY e.change_seq
            ORDER BY tc.trade_date DESC
        ) AS rn_before
    FROM events e
    JOIN trade_calendar tc
      ON tc.trade_date < e.onboarding_date
),
after_calendar_ranked AS (
    SELECT
        e.change_seq,
        tc.trade_date,
        ROW_NUMBER() OVER (
            PARTITION BY e.change_seq
            ORDER BY tc.trade_date ASC
        ) AS rn_after
    FROM events e
    JOIN trade_calendar tc
      ON tc.trade_date > e.onboarding_date
),
before_window AS (
    SELECT
        bc.change_seq,
        bc.trade_date,
        d.fund_ret,
        d.bench_ret
    FROM before_calendar_ranked bc
    JOIN events e
      ON e.change_seq = bc.change_seq
    LEFT JOIN daily_returns d
      ON d.INNERCODE = e.INNERCODE
     AND d.trade_date = bc.trade_date
    WHERE bc.rn_before <= 60
),
after_window AS (
    SELECT
        ac.change_seq,
        ac.trade_date,
        d.fund_ret,
        d.bench_ret
    FROM after_calendar_ranked ac
    JOIN events e
      ON e.change_seq = ac.change_seq
    LEFT JOIN daily_returns d
      ON d.INNERCODE = e.INNERCODE
     AND d.trade_date = ac.trade_date
    WHERE ac.rn_after <= 60
),
before_agg AS (
    SELECT
        change_seq,
        COUNT(*) AS before_calendar_count,
        COUNT(CASE WHEN fund_ret IS NOT NULL AND bench_ret IS NOT NULL THEN 1 END) AS before_valid_count,
        MIN(trade_date) AS before_start,
        MAX(trade_date) AS before_end,
        (
            (EXP(SUM(LN(1 + fund_ret))) - 1)
            -
            (EXP(SUM(LN(1 + bench_ret))) - 1)
        ) * 100 AS excess_return_before_60d_pct
    FROM before_window
    GROUP BY change_seq
),
after_agg AS (
    SELECT
        change_seq,
        COUNT(*) AS after_calendar_count,
        COUNT(CASE WHEN fund_ret IS NOT NULL AND bench_ret IS NOT NULL THEN 1 END) AS after_valid_count,
        MIN(trade_date) AS after_start,
        MAX(trade_date) AS after_end,
        (
            (EXP(SUM(LN(1 + fund_ret))) - 1)
            -
            (EXP(SUM(LN(1 + bench_ret))) - 1)
        ) * 100 AS excess_return_after_60d_pct
    FROM after_window
    GROUP BY change_seq
)
SELECT
    e.new_managers AS `新基金经理名称`,
    DATE_FORMAT(e.onboarding_date, '%Y-%m-%d') AS `任职日期`,
    CONCAT(DATE_FORMAT(b.before_start, '%Y-%m-%d'), '至', DATE_FORMAT(b.before_end, '%Y-%m-%d')) AS `前窗口`,
    CONCAT(DATE_FORMAT(a.after_start, '%Y-%m-%d'), '至', DATE_FORMAT(a.after_end, '%Y-%m-%d')) AS `后窗口`,
    ROUND(b.excess_return_before_60d_pct, 2) AS `前60日累计超额收益（%）`,
    ROUND(a.excess_return_after_60d_pct, 2) AS `后60日累计超额收益（%）`,
    CASE
        WHEN a.excess_return_after_60d_pct > b.excess_return_before_60d_pct THEN '是'
        ELSE '否'
    END AS `是否改善`
FROM events e
JOIN before_agg b
  ON b.change_seq = e.change_seq
JOIN after_agg a
  ON a.change_seq = e.change_seq
WHERE b.before_calendar_count = 60
  AND b.before_valid_count = 60
  AND a.after_calendar_count = 60
  AND a.after_valid_count = 60
ORDER BY e.onboarding_date;
