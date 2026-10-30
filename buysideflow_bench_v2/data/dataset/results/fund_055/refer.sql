WITH params AS (
    SELECT
        DATE('2026-02-13') AS meeting_date,
        1.0 AS spread_threshold_pct
),
target_day AS (
    SELECT MAX(DATE(td.TRADINGDATE)) AS trading_day
    FROM qt_tradingdaynew td
    JOIN params p
      ON td.TRADINGDATE < p.meeting_date
    WHERE td.IFTRADINGDAY = 1
      AND td.SECUMARKET = 83
),
nav_ranked AS (
    SELECT
        f.MAINCODE AS main_fund_code,
        f.SECURITYCODE AS share_fund_code,
        DATE(v.TRADINGDAY) AS trade_date,
        v.NVRDAILYGROWTHRATE AS nvr_daily_growth_rate,
        ROW_NUMBER() OVER (
            PARTITION BY v.INNERCODE, DATE(v.TRADINGDAY)
            ORDER BY v.INSERTTIME DESC, v.UPDATETIME DESC, v.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans v
    JOIN mf_fundarchives f
      ON f.INNERCODE = v.INNERCODE
    JOIN target_day t
      ON DATE(v.TRADINGDAY) = t.trading_day
    JOIN params p
      ON v.INSERTTIME < p.meeting_date
    WHERE f.TYPE IN (2, 3, 4, 7, 8)
      AND f.MAINCODE IS NOT NULL
      AND f.MAINCODE <> ''
      AND (f.ESTABLISHMENTDATE IS NULL OR f.ESTABLISHMENTDATE <= t.trading_day)
      AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE > t.trading_day)
      AND (f.INSERTTIME IS NULL OR f.INSERTTIME < p.meeting_date)
      AND v.NVRDAILYGROWTHRATE IS NOT NULL
),
open_fund_ret AS (
    SELECT
        main_fund_code,
        share_fund_code,
        trade_date,
        nvr_daily_growth_rate
    FROM nav_ranked
    WHERE rn = 1
),
scored AS (
    SELECT
        o.*,
        COUNT(*) OVER (PARTITION BY o.main_fund_code) AS class_cnt,
        MAX(o.nvr_daily_growth_rate) OVER (PARTITION BY o.main_fund_code)
          - MIN(o.nvr_daily_growth_rate) OVER (PARTITION BY o.main_fund_code) AS day_spread_pct
    FROM open_fund_ret o
)
SELECT
    s.main_fund_code,
    s.share_fund_code,
    s.trade_date,
    s.nvr_daily_growth_rate
FROM scored s
JOIN params p
  ON s.day_spread_pct > p.spread_threshold_pct
WHERE s.class_cnt > 1
ORDER BY s.main_fund_code, s.share_fund_code;
