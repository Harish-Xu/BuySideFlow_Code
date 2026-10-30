-- name: valuation_history
WITH
params AS (
    SELECT DATE('{as_of_date}') AS as_of_date
),
target_stocks AS (
    SELECT
        sm.INNERCODE,
        sm.COMPANYCODE,
        sm.SECUCODE,
        sm.SECUMARKET,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, sm.SECUCODE) AS stock_name
    FROM secumain sm
    WHERE sm.SECUCATEGORY = 1
      AND sm.SECUMARKET IN (83, 90)
      AND sm.SECUCODE IN ('600519', '000858', '000568')
),
valuation_history AS (
    SELECT
        ts.SECUCODE,
        ts.stock_name,
        DATE(v.TRADINGDAY) AS trading_day,
        v.PE AS pe_ttm
    FROM target_stocks ts
    JOIN lc_dindicesforvaluation v
      ON v.INNERCODE = ts.INNERCODE
    JOIN qt_tradingdaynew td
      ON DATE(td.TRADINGDATE) = DATE(v.TRADINGDAY)
     AND td.SECUMARKET = ts.SECUMARKET
     AND td.IFTRADINGDAY = 1
    JOIN params p
      ON DATE(v.TRADINGDAY) BETWEEN DATE_SUB(p.as_of_date, INTERVAL 5 YEAR) AND p.as_of_date
    WHERE v.PE > 0
)
SELECT
    v.SECUCODE,
    v.stock_name,
    v.trading_day,
    v.pe_ttm,
    CASE WHEN v.trading_day = p.as_of_date THEN 1 ELSE 0 END AS is_current
FROM valuation_history v
CROSS JOIN params p
ORDER BY v.SECUCODE, v.trading_day;

-- name: annual_profit
WITH
params AS (
    SELECT DATE('{as_of_date}') AS as_of_date
),
target_stocks AS (
    SELECT
        sm.COMPANYCODE,
        sm.SECUCODE,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, sm.SECUCODE) AS stock_name
    FROM secumain sm
    WHERE sm.SECUCATEGORY = 1
      AND sm.SECUMARKET IN (83, 90)
      AND sm.SECUCODE IN ('600519', '000858', '000568')
),
ranked_profit AS (
    SELECT
        ts.SECUCODE,
        ts.stock_name,
        DATE(md.ENDDATE) AS report_date,
        md.NPFROMPARENTCOMPANYOWNERS AS net_profit_parent,
        ROW_NUMBER() OVER (
            PARTITION BY ts.SECUCODE, DATE(md.ENDDATE)
            ORDER BY CASE md.MARK
                         WHEN 1 THEN 1
                         WHEN 8 THEN 2
                         WHEN 7 THEN 3
                         WHEN 6 THEN 4
                         WHEN 2 THEN 5
                         ELSE 9
                     END,
                     md.INFOPUBLDATE DESC,
                     md.INSERTTIME DESC,
                     md.JSID DESC,
                     md.ID DESC
        ) AS rn
    FROM target_stocks ts
    JOIN lc_maindatanew md
      ON md.COMPANYCODE = ts.COMPANYCODE
    JOIN params p
      ON md.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE DATE(md.ENDDATE) IN (DATE('2021-12-31'), DATE('2024-12-31'))
      AND md.MARK IN (1, 8, 7, 6, 2)
)
SELECT
    SECUCODE,
    stock_name,
    report_date,
    net_profit_parent
FROM ranked_profit
WHERE rn = 1
  AND net_profit_parent > 0
ORDER BY SECUCODE, report_date;
