WITH params AS (
    SELECT
        DATE('2025-06-30') AS as_of_date,
        DATE('2025-01-01') AS start_date
),
price_dedup AS (
    SELECT
        cp.INNERCODE,
        DATE(cp.INFOPUBLDATE) AS announcement_date,
        DATE(cp.VALIDDATE) AS valid_date,
        cp.CONVERTPRICE,
        COALESCE(NULLIF(TRIM(BOTH '\\' FROM TRIM(cp.CHANGEREASON)), ''), '未披露') AS change_reason,
        cp.ID,
        ROW_NUMBER() OVER (
            PARTITION BY cp.INNERCODE, DATE(cp.VALIDDATE)
            ORDER BY cp.INFOPUBLDATE DESC, cp.UPDATETIME DESC, cp.ID DESC
        ) AS rn
    FROM bond_conbdconvertprice cp
    JOIN params p
      ON 1 = 1
    WHERE cp.INFOPUBLDATE IS NOT NULL
      AND cp.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND cp.VALIDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND cp.CONVERTPRICE > 0
),
price_seq AS (
    SELECT
        d.*,
        LAG(d.CONVERTPRICE) OVER (
            PARTITION BY d.INNERCODE
            ORDER BY d.valid_date, d.announcement_date, d.ID
        ) AS pre_convert_price
    FROM price_dedup d
    WHERE d.rn = 1
),
revision_events AS (
    SELECT
        ROW_NUMBER() OVER (
            ORDER BY ps.announcement_date DESC, bc.SECUCODE, ps.INNERCODE, ps.valid_date
        ) AS event_id,
        ps.INNERCODE,
        bc.SECUCODE AS bond_code,
        bc.SECUABBR AS bond_abbr,
        bc.SECUMARKET AS secu_market,
        ps.announcement_date,
        ps.valid_date,
        1.0 - ps.CONVERTPRICE / ps.pre_convert_price AS cut_ratio
    FROM price_seq ps
    JOIN params p
      ON 1 = 1
    JOIN bond_code bc
      ON bc.INNERCODE = ps.INNERCODE
    JOIN bond_conbdbasicinfo b
      ON b.INNERCODE = ps.INNERCODE
    WHERE ps.announcement_date >= p.start_date
      AND ps.announcement_date <= p.as_of_date
      AND ps.pre_convert_price IS NOT NULL
      AND ps.CONVERTPRICE < ps.pre_convert_price
      AND ps.change_reason LIKE '%股票价格%'
      AND bc.BONDTYPELEVEL1DESC = '可转债'
      AND bc.BONDTYPELEVEL2DESC = '可转债'
      AND COALESCE(b.BONDFORM, 1) = 1
),
event_quote_ranked AS (
    SELECT
        e.event_id,
        DATE(q.TRADINGDAY) AS trading_day,
        q.CLOSEPRICE,
        q.STOCKPRICE,
        ROW_NUMBER() OVER (
            PARTITION BY e.event_id, DATE(q.TRADINGDAY)
            ORDER BY q.UPDATETIME DESC, q.ID DESC
        ) AS rn
    FROM revision_events e
    JOIN params p
      ON 1 = 1
    JOIN bond_conbdexchangequote q FORCE INDEX (IX_BOND_CONBDEXCHANGEQUOTE)
      ON q.INNERCODE = e.INNERCODE
     AND q.TRADINGDAY < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
),
event_quotes AS (
    SELECT event_id, trading_day, CLOSEPRICE, STOCKPRICE
    FROM event_quote_ranked
    WHERE rn = 1
),
base_dates AS (
    SELECT
        e.event_id,
        MAX(DATE(td.TRADINGDATE)) AS trading_day
    FROM revision_events e
    JOIN qt_tradingdaynew td
      ON td.SECUMARKET = e.secu_market
     AND td.IFTRADINGDAY = 1
     AND td.TRADINGDATE < e.announcement_date
    GROUP BY e.event_id
),
post_dates AS (
    SELECT
        e.event_id,
        DATE(td.TRADINGDATE) AS trading_day,
        ROW_NUMBER() OVER (
            PARTITION BY e.event_id
            ORDER BY td.TRADINGDATE ASC
        ) AS seq
    FROM revision_events e
    JOIN params p
      ON 1 = 1
    JOIN qt_tradingdaynew td
      ON td.SECUMARKET = e.secu_market
     AND td.IFTRADINGDAY = 1
     AND td.TRADINGDATE > e.announcement_date
     AND td.TRADINGDATE <= p.as_of_date
),
event_window AS (
    SELECT
        e.event_id,
        b.trading_day AS base_date,
        p.trading_day AS fifth_date
    FROM revision_events e
    JOIN base_dates b
      ON b.event_id = e.event_id
    JOIN post_dates p
      ON p.event_id = e.event_id
     AND p.seq = 5
)
SELECT
    e.bond_code AS `转债代码`,
    e.bond_abbr AS `转债简称`,
    e.announcement_date AS `下修实施公告日`,
    w.base_date AS `收益基准日`,
    w.fifth_date AS `第5个交易日`,
    ROUND(e.cut_ratio * 100, 2) AS `下修幅度`,
    ROUND((q5.CLOSEPRICE / qb.CLOSEPRICE - 1) * 100, 2) AS `事件窗口转债涨跌幅`,
    ROUND((q5.STOCKPRICE / qb.STOCKPRICE - 1) * 100, 2) AS `事件窗口正股涨跌幅`
FROM revision_events e
JOIN event_window w
  ON w.event_id = e.event_id
JOIN event_quotes qb
  ON qb.event_id = e.event_id
 AND qb.trading_day = w.base_date
JOIN event_quotes q5
  ON q5.event_id = e.event_id
 AND q5.trading_day = w.fifth_date
WHERE qb.CLOSEPRICE > 0
  AND q5.CLOSEPRICE > 0
  AND qb.STOCKPRICE > 0
  AND q5.STOCKPRICE > 0
ORDER BY e.announcement_date DESC, e.bond_code ASC, e.valid_date ASC;
