-- Extracted from fund_098/refer.py (main SQL: NAV_SQL)
-- Template: {{ name }} will be substituted from refer.py before execution.
-- {{ ALL_FUNDS_CSV }} is comma-quoted list, e.g. '022670.OF', '022432.OF'
-- {{ NAV_FETCH_START }} and {{ BACKTEST_END }} are date strings

WITH params AS (
    SELECT DATE('{{ NAV_FETCH_START }}') AS fetch_start,
           DATE('{{ BACKTEST_END }}') AS end_date
),
nav_dedup AS (
    SELECT
        s.SECUCODE,
        s.SECUABBR,
        DATE(f.TRADINGDAY) AS trading_day,
        f.UNITNVRESTORED,
        ROW_NUMBER() OVER (
            PARTITION BY s.SECUCODE, DATE(f.TRADINGDAY)
            ORDER BY f.TRADINGDAY DESC, f.INSERTTIME DESC, f.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans f
    JOIN secumain s
      ON s.INNERCODE = f.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE s.SECUCATEGORY = 8
      AND s.SECUCODE IN ({{ ALL_FUNDS_CSV }})
      AND f.TRADINGDAY >= p.fetch_start
      AND f.TRADINGDAY < DATE_ADD(p.end_date, INTERVAL 1 DAY)
      AND f.UNITNVRESTORED IS NOT NULL
)
SELECT
    SECUCODE,
    SECUABBR,
    trading_day,
    UNITNVRESTORED
FROM nav_dedup
WHERE rn = 1
ORDER BY trading_day, SECUCODE
