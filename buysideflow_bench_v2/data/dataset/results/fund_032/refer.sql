WITH params AS (
    SELECT DATE('2025-12-09') AS anchor_date
),
target_fund AS (
    SELECT
        sm.INNERCODE,
        sm.SECUCODE AS fund_code,
        sm.SECUABBR AS fund_name
    FROM secumain sm
    WHERE sm.SECUCODE = '000477'
      AND sm.SECUCATEGORY = 8
    ORDER BY sm.INNERCODE
    LIMIT 1
),
stock_map AS (
    SELECT
        INNERCODE,
        SECUCODE,
        SECUABBR
    FROM secumain
    UNION ALL
    SELECT
        INNERCODE,
        SECUCODE,
        SECUABBR
    FROM hk_secumain
),
holding_rows AS (
    SELECT
        k.INNERCODE,
        k.REPORTDATE,
        COALESCE(k.INFOPUBLDATE, k.REPORTDATE) AS announcement_date,
        k.SERIALNUMBER,
        k.STOCKINNERCODE,
        k.RATIOINNV,
        k.MARKETVALUE,
        ROW_NUMBER() OVER (
            PARTITION BY k.INNERCODE, k.REPORTDATE, k.SERIALNUMBER
            ORDER BY COALESCE(k.INFOPUBLDATE, k.REPORTDATE) DESC,
                     k.INSERTTIME DESC,
                     k.ID DESC
        ) AS rn
    FROM mf_keystockportfolio k
    JOIN target_fund tf
      ON tf.INNERCODE = k.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE k.REPORTDATE = DATE('2025-09-30')
      AND COALESCE(k.INFOPUBLDATE, k.REPORTDATE) <= p.anchor_date
      AND k.INSERTTIME < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
)
SELECT
    tf.fund_code,
    tf.fund_name,
    DATE(hr.REPORTDATE) AS report_date,
    DATE(hr.announcement_date) AS announcement_date,
    sm.SECUCODE AS stock_code,
    sm.SECUABBR AS stock_name,
    ROUND(hr.RATIOINNV * 100, 2) AS weight_in_nav_pct,
    ROUND(hr.MARKETVALUE, 2) AS holding_market_value
FROM holding_rows hr
JOIN target_fund tf
  ON tf.INNERCODE = hr.INNERCODE
LEFT JOIN stock_map sm
  ON sm.INNERCODE = hr.STOCKINNERCODE
WHERE hr.rn = 1
ORDER BY hr.RATIOINNV DESC, sm.SECUCODE ASC;
