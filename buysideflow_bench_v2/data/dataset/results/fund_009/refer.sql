-- Extracted from fund_009/refer.py (main SQL: NAV_SQL)
-- Template: {{ name }} will be substituted from refer.py before execution.
-- DO NOT change the variable names here without updating refer.py.

WITH type_asof AS (
    SELECT t.INNERCODE, t.SECASSETCATCODE,
           ROW_NUMBER() OVER (PARTITION BY t.INNERCODE
               ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC) AS rn
    FROM mf_jyfundtype t
    WHERE t.STANDARD = 75 AND t.EFFECTIVEDATE <= '2025-08-11'
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > '2025-08-11')
      AND t.INSERTTIME < '2025-08-11'
), fund_pool AS (
    SELECT fa.INNERCODE, fa.SECURITYCODE AS fund_code, sm.SECUABBR AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain sm ON sm.INNERCODE = fa.INNERCODE AND sm.SECUCATEGORY = 8
    JOIN type_asof t ON t.INNERCODE = fa.INNERCODE AND t.rn = 1
    WHERE t.SECASSETCATCODE = 1203
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= '2022-08-08'
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > '2025-08-11')
), nav_ranked AS (
    SELECT f.*, DATE(n.TRADINGDAY) AS trading_day, n.UNITNVRESTORED AS adj_nav,
           ROW_NUMBER() OVER (PARTITION BY f.INNERCODE, DATE(n.TRADINGDAY)
               ORDER BY n.INSERTTIME DESC, n.ID DESC) AS rn
    FROM fund_pool f
    JOIN mf_fundnetvalueretrans n ON n.INNERCODE = f.INNERCODE
    WHERE n.TRADINGDAY >= '2022-08-08' AND n.TRADINGDAY < '2025-08-09'
      AND n.INSERTTIME < '2025-08-11'
)
SELECT INNERCODE, fund_code, fund_name, trading_day, adj_nav
FROM nav_ranked WHERE rn = 1 ORDER BY fund_code, trading_day
