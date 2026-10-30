WITH params AS (
    SELECT
        DATE('2026-01-25') AS as_of_date,
        DATE('2026-01-26') AS as_of_next_day
),
target_fund AS (
    SELECT
        sm.INNERCODE
    FROM secumain sm
    JOIN mf_fundarchives fa
      ON fa.INNERCODE = sm.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE sm.SECUCATEGORY = 8
      AND sm.SECUCODE = '163412'
      AND fa.MAINCODE = fa.SECURITYCODE
      AND (fa.INSERTTIME IS NULL OR fa.INSERTTIME < p.as_of_next_day)
      AND (fa.EXPIREDATE IS NULL OR DATE(fa.EXPIREDATE) >= p.as_of_date)
    ORDER BY sm.INNERCODE
    LIMIT 1
),
target_report AS (
    SELECT MAX(DATE(k.REPORTDATE)) AS report_date
    FROM mf_keystockportfolio k
    JOIN target_fund tf
      ON tf.INNERCODE = k.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE k.SERIALNUMBER BETWEEN 1 AND 10
      AND k.INFOPUBLDATE < p.as_of_next_day
      AND (k.INSERTTIME IS NULL OR k.INSERTTIME < p.as_of_next_day)
),
holdings_ranked AS (
    SELECT
        k.INNERCODE,
        DATE(k.REPORTDATE) AS report_date,
        k.SERIALNUMBER,
        k.STOCKINNERCODE,
        k.MARKETVALUE,
        ROW_NUMBER() OVER (
            PARTITION BY k.INNERCODE, DATE(k.REPORTDATE), k.SERIALNUMBER
            ORDER BY
                COALESCE(k.INFOPUBLDATE, DATE('1900-01-01')) DESC,
                COALESCE(k.INSERTTIME, DATE('1900-01-01')) DESC,
                k.XGRQ DESC,
                k.ID DESC
        ) AS rn
    FROM mf_keystockportfolio k
    JOIN target_report tr
      ON DATE(k.REPORTDATE) = tr.report_date
    JOIN params p
      ON 1 = 1
    WHERE k.SERIALNUMBER BETWEEN 1 AND 10
      AND k.INFOPUBLDATE < p.as_of_next_day
      AND (k.INSERTTIME IS NULL OR k.INSERTTIME < p.as_of_next_day)
),
visible_holdings AS (
    SELECT
        INNERCODE,
        report_date,
        SERIALNUMBER,
        STOCKINNERCODE,
        MARKETVALUE
    FROM holdings_ranked
    WHERE rn = 1
      AND STOCKINNERCODE IS NOT NULL
      AND MARKETVALUE > 0
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
    JOIN target_report tr
      ON 1 = 1
    JOIN params p
      ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= tr.report_date
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > tr.report_date)
      AND t.INSERTTIME < p.as_of_next_day
),
active_equity_main_funds AS (
    SELECT fa.INNERCODE
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN type_asof ta
      ON ta.INNERCODE = fa.INNERCODE
     AND ta.rn = 1
    JOIN target_report tr
      ON 1 = 1
    JOIN params p
      ON 1 = 1
    WHERE fa.MAINCODE = fa.SECURITYCODE
      AND fa.INVESTMENTTYPE = 16
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND ta.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
      AND (fa.INSERTTIME IS NULL OR fa.INSERTTIME < p.as_of_next_day)
      AND (fa.EXPIREDATE IS NULL OR DATE(fa.EXPIREDATE) >= tr.report_date)
),
fund_stock_value AS (
    SELECT
        vh.INNERCODE,
        vh.STOCKINNERCODE,
        SUM(vh.MARKETVALUE) AS fund_stock_market_value
    FROM visible_holdings vh
    JOIN active_equity_main_funds af
      ON af.INNERCODE = vh.INNERCODE
    GROUP BY vh.INNERCODE, vh.STOCKINNERCODE
),
popular_pool AS (
    SELECT STOCKINNERCODE
    FROM (
        SELECT
            fsv.STOCKINNERCODE,
            SUM(fsv.fund_stock_market_value) AS total_market_value,
            ROW_NUMBER() OVER (
                ORDER BY SUM(fsv.fund_stock_market_value) DESC, fsv.STOCKINNERCODE ASC
            ) AS rn
        FROM fund_stock_value fsv
        GROUP BY fsv.STOCKINNERCODE
    ) ranked
    WHERE rn <= 50
),
target_stock_value AS (
    SELECT
        vh.STOCKINNERCODE,
        SUM(vh.MARKETVALUE) AS target_stock_market_value
    FROM visible_holdings vh
    JOIN target_fund tf
      ON tf.INNERCODE = vh.INNERCODE
    GROUP BY vh.STOCKINNERCODE
)
SELECT
    tr.report_date AS 报告期,
    CAST(SUM(CASE WHEN pp.STOCKINNERCODE IS NOT NULL THEN 1 ELSE 0 END) AS UNSIGNED) AS 命中股票数,
    ROUND(
        SUM(CASE WHEN pp.STOCKINNERCODE IS NOT NULL THEN tsv.target_stock_market_value ELSE 0 END)
        / NULLIF(SUM(tsv.target_stock_market_value), 0) * 100,
        2
    ) AS `命中市值占比（%）`
FROM target_fund tf
JOIN target_report tr
  ON 1 = 1
JOIN target_stock_value tsv
  ON 1 = 1
LEFT JOIN popular_pool pp
  ON pp.STOCKINNERCODE = tsv.STOCKINNERCODE
GROUP BY tr.report_date;
