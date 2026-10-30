-- refer.py runs these three narrow extraction queries separately, then performs
-- the same-day merge, monthly trigger detection, and index-window aggregation in Python.

-- 1) FR007 daily observations, in percent.
SELECT
    DATE(ENDDATE) AS trade_date,
    FR007 AS fr007_pct,
    UPDATETIME AS record_update_time,
    ID AS record_id
FROM bond_baseratereference FORCE INDEX (IX_BOND_BASERATEREFERENCE)
WHERE ENDDATE >= '2022-01-01'
  AND ENDDATE < '2025-01-01'
  AND FR007 IS NOT NULL;

-- 2) 3Y China government bond curve, CURVECODE=10 and YIELDTYPECODE=1.
--    YIELD is stored as a decimal rate in the live table, so it is multiplied by 100
--    to align with FR007's percent unit.
SELECT
    DATE(ENDDATE) AS trade_date,
    YIELD * 100 AS treasury_3y_pct,
    UPDATETIME AS record_update_time,
    ID AS record_id
FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
WHERE ENDDATE >= '2022-01-01'
  AND ENDDATE < '2025-01-01'
  AND CURVECODE = 10
  AND YIELDTYPECODE = 1
  AND YEARSTOMATURITY = 3
  AND YIELD IS NOT NULL;

-- 3) ChinaBond government-bond total index quotes:
--    CBA00621 = 中债-国债总指数_1-3年, CBA00651 = 中债-国债总指数_7-10年.
SELECT
    CASE
        WHEN INNERCODE = 3959 THEN 'CBA00621'
        WHEN INNERCODE = 3962 THEN 'CBA00651'
    END AS index_code,
    DATE(TRADINGDAY) AS trade_date,
    CLOSEPRICE AS close_price,
    UPDATETIME AS record_update_time,
    ID AS record_id
FROM bond_chinabondindexquote
WHERE TRADINGDAY >= '2022-02-01'
  AND TRADINGDAY < '2025-04-01'
  AND INNERCODE IN (3959, 3962)
  AND CLOSEPRICE IS NOT NULL
  AND CLOSEPRICE > 0;
