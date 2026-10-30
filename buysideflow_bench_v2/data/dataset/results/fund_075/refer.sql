WITH params AS (
    SELECT
        DATE('2022-01-01') AS start_day,
        DATE('2024-01-01') AS as_of_next_day,
        DATE('2023-12-31') AS anchor_day
),
hs300 AS (
    SELECT DISTINCT INNERCODE
    FROM secumain
    WHERE SECUCODE = '000300'
      AND SECUCATEGORY = 4
),
hs300_down AS (
    SELECT q.TRADINGDAY AS trading_day
    FROM hs300 i
    JOIN qt_indexquote q FORCE INDEX (IDX_QTIQ_INNERCODE_TRADINGDAY)
      ON q.INNERCODE = i.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE q.TRADINGDAY >= p.start_day
      AND q.TRADINGDAY < p.as_of_next_day
      AND q.CHANGEPCT < 0
    GROUP BY q.TRADINGDAY
),
down_count AS (
    SELECT COUNT(*) AS total_down_days
    FROM hs300_down
),
type_asof AS (
    SELECT
        ft.INNERCODE,
        ft.SECASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY ft.INNERCODE
            ORDER BY ft.EFFECTIVEDATE DESC, ft.INSERTTIME DESC, ft.ID DESC
        ) AS rn
    FROM mf_jyfundtype ft FORCE INDEX (IX_MF_JYFUNDTYPE)
    JOIN params p
      ON 1 = 1
    WHERE ft.STANDARD = 75
      AND ft.EFFECTIVEDATE <= p.anchor_day
      AND (ft.CANCELDATE IS NULL OR ft.CANCELDATE > p.anchor_day)
      AND ft.INSERTTIME < p.as_of_next_day
),
active_equity_main AS (
    SELECT DISTINCT
        fa.INNERCODE,
        fa.SECURITYCODE,
        sm.SECUABBR
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
    JOIN type_asof ta
      ON ta.INNERCODE = fa.INNERCODE
     AND ta.rn = 1
    JOIN params p
      ON 1 = 1
    WHERE ta.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
      AND fa.INVESTMENTTYPE = 16
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.TYPE IN (2, 3, 4, 7, 8)
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND fa.ESTABLISHMENTDATE < p.start_day
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > p.anchor_day)
),
nav_dedup AS (
    SELECT
        nv.INNERCODE,
        nv.TRADINGDAY AS trading_day,
        nv.NVRDAILYGROWTHRATE,
        ROW_NUMBER() OVER (
            PARTITION BY nv.INNERCODE, nv.TRADINGDAY
            ORDER BY nv.INSERTTIME DESC, nv.ID DESC
        ) AS rn
    FROM active_equity_main a
    JOIN mf_fundnetvalueretrans nv FORCE INDEX (IX_MF_FUNDNETVALUERETRANS)
      ON nv.INNERCODE = a.INNERCODE
    JOIN params p
      ON nv.TRADINGDAY >= p.start_day
     AND nv.TRADINGDAY < p.as_of_next_day
    JOIN hs300_down d
      ON d.trading_day = nv.TRADINGDAY
    WHERE nv.NVRDAILYGROWTHRATE IS NOT NULL
      AND nv.INSERTTIME < p.as_of_next_day
),
fund_stats AS (
    SELECT
        a.SECURITYCODE AS fund_code,
        a.SECUABBR AS fund_abbr,
        COUNT(*) AS matched_days,
        SUM(CASE WHEN nd.NVRDAILYGROWTHRATE > 0 THEN 1 ELSE 0 END) AS pos_days
    FROM active_equity_main a
    JOIN nav_dedup nd
      ON nd.INNERCODE = a.INNERCODE
     AND nd.rn = 1
    GROUP BY a.SECURITYCODE, a.SECUABBR
)
SELECT
    fs.fund_code,
    fs.fund_abbr,
    ROUND(fs.pos_days / fs.matched_days, 4) AS pos_return_ratio
FROM fund_stats fs
CROSS JOIN down_count dc
WHERE fs.matched_days = dc.total_down_days
  AND fs.pos_days / fs.matched_days > 0.5
ORDER BY pos_return_ratio DESC, fs.fund_code ASC;
