WITH params AS (
    SELECT
        DATE('2024-12-31') AS as_of_date,
        DATE('2025-01-01') AS next_date
),
base_day AS (
    SELECT MAX(q.TRADINGDAY) AS trading_day
    FROM bond_conbdexchangequote q
    JOIN params p
      ON q.TRADINGDAY <= p.as_of_date
),
quote_ranked AS (
    SELECT
        q.INNERCODE,
        q.CLOSEPRICE AS close_price,
        q.CONVERTPREMIUMRATE,
        ROW_NUMBER() OVER (
            PARTITION BY q.INNERCODE
            ORDER BY q.UPDATETIME DESC, q.ID DESC
        ) AS rn
    FROM bond_conbdexchangequote q
    JOIN base_day b
      ON q.TRADINGDAY = b.trading_day
    WHERE q.CLOSEPRICE IS NOT NULL
      AND q.CLOSEPRICE > 0
      AND q.CONVERTPREMIUMRATE IS NOT NULL
),
call_terms AS (
    SELECT
        c.INNERCODE,
        CASE WHEN c.CALLLEVEL <= 10 THEN c.CALLLEVEL * 100 ELSE c.CALLLEVEL END AS call_trigger_ratio_pct,
        ROW_NUMBER() OVER (
            PARTITION BY c.INNERCODE
            ORDER BY c.CALLSTARTDATE DESC, c.PAYMENTYEARSEQUENCE DESC, c.ID DESC
        ) AS rn
    FROM bond_conbdcallinfo c
    JOIN params p
      ON 1 = 1
    WHERE c.CALLLEVEL IS NOT NULL
      AND c.INSERTTIME IS NOT NULL
      AND c.INSERTTIME < p.next_date
),
put_terms AS (
    SELECT
        t.INNERCODE,
        CASE WHEN t.PUTLEVEL <= 10 THEN t.PUTLEVEL * 100 ELSE t.PUTLEVEL END AS put_trigger_ratio_pct,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY t.PUTSTARTDATE DESC, t.ID DESC
        ) AS rn
    FROM bond_conbdputinfo t
    JOIN params p
      ON 1 = 1
    WHERE t.PUTLEVEL IS NOT NULL
      AND t.INSERTTIME IS NOT NULL
      AND t.INSERTTIME < p.next_date
)
SELECT
    bc.SECUCODE AS `证券代码`,
    bc.SECUABBR AS `债券简称`,
    ROUND(c.call_trigger_ratio_pct, 4) AS `赎回触发比例`,
    ROUND(t.put_trigger_ratio_pct, 4) AS `回售触发比例`,
    q.CONVERTPREMIUMRATE AS `转股溢价率`,
    q.close_price AS `最新收盘价`
FROM quote_ranked q
JOIN bond_code bc
  ON bc.INNERCODE = q.INNERCODE
JOIN bond_conbdbasicinfo b
  ON b.INNERCODE = q.INNERCODE
JOIN call_terms c
  ON c.INNERCODE = q.INNERCODE
 AND c.rn = 1
JOIN put_terms t
  ON t.INNERCODE = q.INNERCODE
 AND t.rn = 1
JOIN params p
  ON 1 = 1
WHERE q.rn = 1
  AND bc.BONDTYPELEVEL1DESC = '可转债'
  AND bc.BONDTYPELEVEL2DESC = '可转债'
  AND b.BONDFORM = 1
  AND c.call_trigger_ratio_pct <= 130
  AND t.put_trigger_ratio_pct >= 70
  AND (bc.LISTEDDATE IS NULL OR bc.LISTEDDATE <= p.as_of_date)
  AND (bc.INTERESTENDDATE IS NULL OR bc.INTERESTENDDATE > p.as_of_date)
  AND (bc.DELISTDATE IS NULL OR bc.DELISTDATE > p.as_of_date)
  AND (b.DELISTDATE IS NULL OR b.DELISTDATE > p.as_of_date)
ORDER BY q.CONVERTPREMIUMRATE ASC, bc.SECUCODE ASC
LIMIT 30;
