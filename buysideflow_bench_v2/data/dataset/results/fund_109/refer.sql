WITH params AS (
    SELECT
        DATE('2023-01-01') AS start_date,
        DATE('2025-12-31') AS anchor_date
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
      AND t.EFFECTIVEDATE <= p.anchor_date
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.anchor_date)
      AND t.INSERTTIME < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
),
manager_asof AS (
    SELECT DISTINCT m.INNERCODE
    FROM mf_fundmanagernew m
    JOIN params p
      ON 1 = 1
    WHERE m.NAME = '孙轶佳'
      AND m.POSTNAME = 1
      AND m.ACCESSIONDATE <= p.anchor_date
      AND (m.DIMISSIONDATE IS NULL OR m.DIMISSIONDATE > p.anchor_date)
),
fund_pool AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS main_fund_code,
        sm.SECUABBR AS fund_name
    FROM manager_asof m
    JOIN mf_fundarchives fa
      ON fa.INNERCODE = m.INNERCODE
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN type_asof t
      ON t.INNERCODE = fa.INNERCODE
     AND t.rn = 1
    JOIN params p
      ON 1 = 1
    WHERE fa.INVESTADVISORCODE = 41511
      AND t.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
      AND fa.INVESTMENTTYPE = 16
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE < p.start_date
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.anchor_date)
      AND (fa.ENCLEARINGDATE IS NULL OR fa.ENCLEARINGDATE >= p.anchor_date)
      AND (fa.LASTOPERATIONDATE IS NULL OR fa.LASTOPERATIONDATE >= p.anchor_date)
),
nav_ranked AS (
    SELECT
        f.main_fund_code,
        f.fund_name,
        DATE(n.TRADINGDAY) AS trading_day,
        n.UNITNVRESTORED AS restored_nav,
        ROW_NUMBER() OVER (
            PARTITION BY n.INNERCODE, DATE(n.TRADINGDAY)
            ORDER BY n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM fund_pool f
    JOIN mf_fundnetvalueretrans n
      ON n.INNERCODE = f.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE n.TRADINGDAY >= p.start_date
      AND n.TRADINGDAY < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
      AND n.INSERTTIME < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
      AND n.UNITNVRESTORED > 0
      AND EXISTS (
          SELECT 1
          FROM qt_tradingdaynew td
          WHERE td.SECUMARKET = 83
            AND td.IFTRADINGDAY = 1
            AND td.TRADINGDATE = DATE(n.TRADINGDAY)
      )
)
SELECT
    main_fund_code,
    fund_name,
    trading_day,
    restored_nav
FROM nav_ranked
WHERE rn = 1
ORDER BY main_fund_code, trading_day;
