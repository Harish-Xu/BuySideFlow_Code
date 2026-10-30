-- Extracted from bond_055/refer.py (main SQL: SQL)
-- Template: {{ name }} will be substituted from refer.py before execution.
-- DO NOT change the variable names here without updating refer.py.

WITH price_daily AS (
    SELECT
        INNERCODE,
        DATE(VALIDDATE) AS signal_date,
        CONVERTPRICE,
        COALESCE(NULLIF(TRIM(CHANGEREASON), ''), '未披露') AS change_reason,
        ID,
        ROW_NUMBER() OVER (
            PARTITION BY INNERCODE, DATE(VALIDDATE)
            ORDER BY VALIDDATE DESC, ID DESC
        ) AS rn
    FROM bond_conbdconvertprice
    WHERE VALIDDATE < '{{ END_DATE }}'
      AND INFOPUBLDATE < DATE_ADD(DATE(VALIDDATE), INTERVAL 1 DAY)
      AND CONVERTPRICE IS NOT NULL
),
price_seq AS (
    SELECT
        INNERCODE,
        signal_date,
        CONVERTPRICE,
        change_reason,
        ID,
        LAG(CONVERTPRICE) OVER (
            PARTITION BY INNERCODE
            ORDER BY signal_date, ID
        ) AS prev_convert_price
    FROM price_daily
    WHERE rn = 1
),
events AS (
    SELECT
        ROW_NUMBER() OVER (ORDER BY signal_date, INNERCODE) AS event_id,
        signal_date,
        INNERCODE AS asset_id,
        change_reason,
        prev_convert_price,
        CONVERTPRICE AS new_convert_price
    FROM price_seq
    WHERE signal_date >= '{{ START_DATE }}'
      AND signal_date < '{{ END_DATE }}'
      AND prev_convert_price IS NOT NULL
      AND CONVERTPRICE < prev_convert_price
),
trading_calendar AS (
    SELECT
        trade_date,
        LEAD(trade_date, {{ HOLDING_DAYS }}) OVER (ORDER BY trade_date) AS exit_date
    FROM (
        SELECT DISTINCT DATE(TRADINGDATE) AS trade_date
        FROM qt_tradingdaynew
        WHERE SECUMARKET IN (83, 90)
          AND IFTRADINGDAY = 1
          AND TRADINGDATE >= '{{ START_DATE }}'
          AND TRADINGDATE < '{{ QUOTE_END_DATE }}'
    ) d
),
event_starts AS (
    SELECT e.event_id, MIN(t.trade_date) AS entry_date
    FROM events e
    JOIN trading_calendar t ON t.trade_date > e.signal_date
    GROUP BY e.event_id
),
event_dates AS (
    SELECT e.*, s.entry_date, t.exit_date
    FROM events e
    JOIN event_starts s ON s.event_id = e.event_id
    JOIN trading_calendar t ON t.trade_date = s.entry_date
    WHERE t.exit_date IS NOT NULL
),
event_quotes AS (
    SELECT
        e.event_id,
        e.signal_date,
        e.asset_id,
        e.change_reason,
        e.prev_convert_price,
        e.new_convert_price,
        e.entry_date,
        e.exit_date,
        DATE(q.TRADINGDAY) AS trade_date,
        q.CLOSEPRICE,
        ROW_NUMBER() OVER (
            PARTITION BY e.event_id, DATE(q.TRADINGDAY)
            ORDER BY q.ID DESC
        ) AS quote_version
    FROM event_dates e
    JOIN bond_conbdexchangequote q
      ON e.asset_id = q.INNERCODE
     AND q.TRADINGDAY IN (e.entry_date, e.exit_date)
     AND q.CLOSEPRICE IS NOT NULL
     AND q.CLOSEPRICE > 0
)
SELECT
    event_id,
    signal_date,
    asset_id,
    change_reason,
    prev_convert_price,
    new_convert_price,
    entry_date,
    MAX(CASE WHEN trade_date = entry_date THEN CLOSEPRICE END) AS entry_price,
    exit_date,
    MAX(CASE WHEN trade_date = exit_date THEN CLOSEPRICE END) AS exit_price
FROM event_quotes
WHERE quote_version = 1
GROUP BY
    event_id,
    signal_date,
    asset_id,
    change_reason,
    prev_convert_price,
    new_convert_price,
    entry_date,
    exit_date
HAVING entry_price IS NOT NULL
   AND exit_price IS NOT NULL
ORDER BY signal_date, asset_id;
