WITH params AS (
    SELECT
        DATE('2025-06-30') AS as_of_date,
        TIMESTAMP('2025-07-01 00:00:00') AS next_day_ts
),
obs_day AS (
    SELECT MAX(q.TRADINGDAY) AS obs_date
    FROM bond_conbdexchangequote q
    JOIN params p
      ON q.TRADINGDAY <= p.as_of_date
),
obs_quote_ranked AS (
    SELECT
        q.INNERCODE,
        q.CLOSEPRICE,
        q.NEWCONVETPRICE,
        ROW_NUMBER() OVER (
            PARTITION BY q.INNERCODE
            ORDER BY q.UPDATETIME DESC, q.ID DESC
        ) AS rn
    FROM bond_conbdexchangequote q FORCE INDEX (IX_BOND_CONBDEXCHANGEQUOTE)
    JOIN obs_day o
      ON q.TRADINGDAY = o.obs_date
    WHERE q.CLOSEPRICE IS NOT NULL
      AND q.NEWCONVETPRICE IS NOT NULL
      AND q.NEWCONVETPRICE > 0
),
active_universe AS (
    SELECT
        bc.INNERCODE,
        bc.MAINCODE,
        bc.SECUCODE AS convertible_code,
        bc.SECUABBR AS convertible_abbr,
        b.STOCKINNERCODE AS stock_innercode,
        COALESCE(s.SECUCODE, b.CONVCODE) AS underlying_code,
        q.NEWCONVETPRICE AS obs_convert_price
    FROM obs_quote_ranked q
    JOIN bond_code bc
      ON q.INNERCODE = bc.INNERCODE
    JOIN bond_conbdbasicinfo b
      ON q.INNERCODE = b.INNERCODE
    LEFT JOIN secumain s
      ON b.STOCKINNERCODE = s.INNERCODE
    JOIN obs_day o
      ON 1 = 1
    JOIN params p
      ON 1 = 1
    WHERE q.rn = 1
      AND bc.SECUMARKET IN (83, 90)
      AND bc.BONDTYPELEVEL1DESC = '可转债'
      AND bc.BONDTYPELEVEL2DESC = '可转债'
      AND b.STOCKINNERCODE IS NOT NULL
      AND (
          b.INITIALINFOPUBLDATE IS NULL
          OR b.INITIALINFOPUBLDATE < p.next_day_ts
      )
      AND (bc.LISTEDDATE IS NULL OR DATE(bc.LISTEDDATE) <= DATE(o.obs_date))
      AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) >= DATE(o.obs_date))
      AND (b.LISTEDDATE IS NULL OR DATE(b.LISTEDDATE) <= DATE(o.obs_date))
      AND (b.ENDDATE IS NULL OR DATE(b.ENDDATE) >= DATE(o.obs_date))
      AND (b.LISTEDENDDATE IS NULL OR DATE(b.LISTEDENDDATE) >= DATE(o.obs_date))
      AND (b.TRADEENDDATE IS NULL OR DATE(b.TRADEENDDATE) >= DATE(o.obs_date))
      AND (b.LASTTRADINGDATE IS NULL OR DATE(b.LASTTRADINGDATE) >= DATE(o.obs_date))
      AND (b.DELISTDATE IS NULL OR DATE(b.DELISTDATE) >= DATE(o.obs_date))
      AND bc.SECUABBR NOT LIKE '%退%'
),
call_terms AS (
    SELECT
        x.INNERCODE,
        x.call_start_date,
        x.call_trigger_ratio,
        x.call_window_days,
        x.required_reach_days
    FROM (
        SELECT
            c.INNERCODE,
            DATE(c.CALLSTARTDATE) AS call_start_date,
            CASE
                WHEN c.CALLLEVEL > 10 THEN c.CALLLEVEL / 100
                ELSE c.CALLLEVEL
            END AS call_trigger_ratio,
            CAST(c.CALLCONDITIONDAY AS UNSIGNED) AS call_window_days,
            CAST(c.CALLREACHDAY AS UNSIGNED) AS required_reach_days,
            ROW_NUMBER() OVER (
                PARTITION BY c.INNERCODE
                ORDER BY
                    c.CALLSTARTDATE DESC,
                    c.CALLENDDATE DESC,
                    c.PAYMENTYEARSEQUENCE DESC,
                    c.ID DESC
            ) AS rn
        FROM bond_conbdcallinfo c FORCE INDEX (IX_BOND_CONBDCALLINFO)
        JOIN active_universe u
          ON c.INNERCODE = u.INNERCODE
        JOIN obs_day o
          ON DATE(c.CALLSTARTDATE) <= DATE(o.obs_date)
        JOIN params p
          ON c.INSERTTIME < p.next_day_ts
        WHERE c.CALLSTARTDATE IS NOT NULL
          AND (c.CALLENDDATE IS NULL OR DATE(c.CALLENDDATE) >= DATE(o.obs_date))
          AND c.CALLLEVEL IS NOT NULL
          AND c.CALLLEVEL > 0
          AND c.CALLCONDITIONDAY IS NOT NULL
          AND c.CALLCONDITIONDAY > 0
          AND c.CALLREACHDAY IS NOT NULL
          AND c.CALLREACHDAY > 0
    ) x
    WHERE x.rn = 1
),
window_quote_dedup AS (
    SELECT
        y.INNERCODE,
        y.TRADINGDAY,
        y.stock_close_price,
        y.NEWCONVETPRICE,
        y.call_trigger_ratio,
        y.call_window_days,
        y.required_reach_days,
        ROW_NUMBER() OVER (
            PARTITION BY y.INNERCODE
            ORDER BY y.TRADINGDAY DESC
        ) AS window_rn
    FROM (
        SELECT
            q.INNERCODE,
            q.TRADINGDAY,
            COALESCE(sq.CLOSEPRICE, stib.CLOSEPRICE) AS stock_close_price,
            q.NEWCONVETPRICE,
            c.call_trigger_ratio,
            c.call_window_days,
            c.required_reach_days,
            ROW_NUMBER() OVER (
                PARTITION BY q.INNERCODE, q.TRADINGDAY
                ORDER BY q.UPDATETIME DESC, q.ID DESC
            ) AS rn
        FROM bond_conbdexchangequote q FORCE INDEX (IX_BOND_CONBDEXCHANGEQUOTE)
        JOIN call_terms c
          ON q.INNERCODE = c.INNERCODE
         AND DATE(q.TRADINGDAY) >= c.call_start_date
        JOIN active_universe u
          ON q.INNERCODE = u.INNERCODE
        JOIN params p
          ON 1 = 1
        LEFT JOIN qt_dailyquote sq
          ON u.stock_innercode = sq.INNERCODE
         AND q.TRADINGDAY = sq.TRADINGDAY
        LEFT JOIN lc_stibdailyquote stib
          ON u.stock_innercode = stib.INNERCODE
         AND q.TRADINGDAY = stib.TRADINGDAY
         AND stib.INSERTTIME < p.next_day_ts
        JOIN obs_day o
          ON q.TRADINGDAY <= o.obs_date
        WHERE COALESCE(sq.CLOSEPRICE, stib.CLOSEPRICE) IS NOT NULL
          AND COALESCE(sq.CLOSEPRICE, stib.CLOSEPRICE) > 0
          AND q.NEWCONVETPRICE IS NOT NULL
          AND q.NEWCONVETPRICE > 0
    ) y
    WHERE y.rn = 1
),
call_progress AS (
    SELECT
        INNERCODE,
        MAX(call_window_days) AS call_window_days,
        MAX(required_reach_days) AS required_reach_days,
        SUM(
            CASE
                WHEN stock_close_price >= NEWCONVETPRICE * call_trigger_ratio THEN 1
                ELSE 0
            END
        ) AS reached_days
    FROM window_quote_dedup
    WHERE window_rn <= call_window_days
    GROUP BY INNERCODE
),
latest_size AS (
    SELECT
        z.MAINCODE,
        z.total_size_million
    FROM (
        SELECT
            s.MAINCODE,
            s.TOTALSIZE AS total_size_million,
            ROW_NUMBER() OVER (
                PARTITION BY s.MAINCODE
                ORDER BY s.ENDDATE DESC, s.INFOPUBLDATE DESC, s.ID DESC
            ) AS rn
        FROM bond_size s FORCE INDEX (IX_BOND_SIZE)
        JOIN active_universe u
          ON s.MAINCODE = u.MAINCODE
        JOIN obs_day o
          ON s.ENDDATE <= o.obs_date
        WHERE s.TOTALSIZE IS NOT NULL
          AND s.TOTALSIZE > 0
          AND (s.INFOPUBLDATE IS NULL OR s.INFOPUBLDATE <= o.obs_date)
    ) z
    WHERE z.rn = 1
),
final_result AS (
    SELECT
        u.convertible_code AS `转债代码`,
        u.convertible_abbr AS `转债简称`,
        u.underlying_code AS `正股代码`,
        ROUND(u.obs_convert_price * c.call_trigger_ratio, 2) AS `当日触发价`,
        c.call_window_days AS `观察窗口`,
        c.required_reach_days AS `要求天数`,
        cp.reached_days AS `已满足天数`,
        GREATEST(c.required_reach_days - cp.reached_days, 0) AS remaining_days,
        ROUND(ls.total_size_million / 100.0, 2) AS `剩余债券余额(亿元)`
    FROM active_universe u
    JOIN call_terms c
      ON u.INNERCODE = c.INNERCODE
    JOIN call_progress cp
      ON u.INNERCODE = cp.INNERCODE
    JOIN latest_size ls
      ON u.MAINCODE = ls.MAINCODE
)
SELECT
    `转债代码`,
    `转债简称`,
    `正股代码`,
    `当日触发价`,
    `观察窗口`,
    `要求天数`,
    `已满足天数`,
    remaining_days AS `还差天数`,
    `剩余债券余额(亿元)`
FROM final_result
WHERE remaining_days <= 5
ORDER BY
    remaining_days ASC,
    `已满足天数` DESC,
    `剩余债券余额(亿元)` DESC,
    `转债代码` ASC;
