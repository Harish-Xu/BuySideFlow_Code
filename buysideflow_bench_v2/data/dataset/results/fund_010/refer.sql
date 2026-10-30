WITH params AS (
    SELECT
        DATE('2025-09-03') AS anchor_day,
        DATE_SUB(DATE('2025-09-03'), INTERVAL 1 YEAR) AS start_day,
        DATE('2025-06-30') AS holding_report_date
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
      AND t.EFFECTIVEDATE <= p.anchor_day
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.anchor_day)
      AND t.INSERTTIME < DATE_ADD(p.anchor_day, INTERVAL 1 DAY)
),
fund_pool AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        sm.SECUABBR AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN type_asof ta
      ON ta.INNERCODE = fa.INNERCODE
     AND ta.rn = 1
    JOIN params p
      ON 1 = 1
    WHERE ta.SECASSETCATCODE = 1201
      AND fa.TYPE = 2
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= p.anchor_day
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.anchor_day)
),
nav_dedup AS (
    SELECT
        fp.INNERCODE,
        fp.fund_code,
        fp.fund_name,
        DATE(n.TRADINGDAY) AS trading_day,
        n.UNITNVRESTORED,
        ROW_NUMBER() OVER (
            PARTITION BY fp.INNERCODE, DATE(n.TRADINGDAY)
            ORDER BY n.TRADINGDAY DESC, n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans n
    JOIN fund_pool fp
      ON fp.INNERCODE = n.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE n.UNITNVRESTORED IS NOT NULL
      AND n.TRADINGDAY >= p.start_day
      AND n.TRADINGDAY <= p.anchor_day
      AND n.INSERTTIME < DATE_ADD(p.anchor_day, INTERVAL 1 DAY)
),
nav_series AS (
    SELECT
        INNERCODE,
        fund_code,
        fund_name,
        trading_day,
        UNITNVRESTORED AS adj_nav
    FROM nav_dedup
    WHERE rn = 1
),
nav_ranked AS (
    SELECT
        INNERCODE,
        fund_code,
        fund_name,
        trading_day,
        adj_nav,
        ROW_NUMBER() OVER (
            PARTITION BY INNERCODE
            ORDER BY trading_day ASC
        ) AS rn_start,
        ROW_NUMBER() OVER (
            PARTITION BY INNERCODE
            ORDER BY trading_day DESC
        ) AS rn_end
    FROM nav_series
),
return_1y AS (
    SELECT
        INNERCODE,
        fund_code,
        fund_name,
        (
            MAX(CASE WHEN rn_end = 1 THEN adj_nav END) /
            NULLIF(MAX(CASE WHEN rn_start = 1 THEN adj_nav END), 0) - 1
        ) AS return_1y
    FROM nav_ranked
    GROUP BY INNERCODE, fund_code, fund_name
    HAVING MAX(CASE WHEN rn_end = 1 THEN trading_day END) > MAX(CASE WHEN rn_start = 1 THEN trading_day END)
),
top100 AS (
    SELECT
        t.*,
        DENSE_RANK() OVER (ORDER BY t.return_1y DESC) AS return_rank
    FROM return_1y t
),
holding_ranked AS (
    SELECT
        t.INNERCODE,
        p.holding_report_date AS report_date,
        sp.STOCKINNERCODE,
        sp.RATIOINNV AS stock_weight,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE, DATE(sp.REPORTDATE), sp.STOCKINNERCODE
            ORDER BY sp.INFOPUBLDATE DESC, sp.INSERTTIME DESC, sp.ID DESC
        ) AS rn
    FROM mf_stockportfoliodetail sp
    JOIN top100 t
      ON t.INNERCODE = sp.INNERCODE
     AND t.return_rank <= 100
    JOIN params p
      ON 1 = 1
    WHERE DATE(sp.REPORTDATE) = p.holding_report_date
      AND sp.STOCKINNERCODE IS NOT NULL
      AND sp.RATIOINNV IS NOT NULL
      AND sp.INFOPUBLDATE < DATE_ADD(p.anchor_day, INTERVAL 1 DAY)
      AND sp.INSERTTIME < DATE_ADD(p.anchor_day, INTERVAL 1 DAY)
),
holding_by_stock AS (
    SELECT INNERCODE, report_date, STOCKINNERCODE, stock_weight
    FROM holding_ranked
    WHERE rn = 1
),
holding_rank AS (
    SELECT
        hbs.INNERCODE,
        hbs.report_date,
        hbs.stock_weight,
        ROW_NUMBER() OVER (
            PARTITION BY hbs.INNERCODE
            ORDER BY hbs.stock_weight DESC, hbs.STOCKINNERCODE
        ) AS stock_rank
    FROM holding_by_stock hbs
),
holding_metric AS (
    SELECT
        INNERCODE,
        report_date,
        SUM(CASE WHEN stock_rank <= 10 THEN stock_weight ELSE 0 END) * 100 AS top10_weight_pct,
        SUM(POWER(stock_weight, 2)) AS hhi
    FROM holding_rank
    GROUP BY INNERCODE, report_date
)
SELECT
    t.fund_code AS fund_code,
    t.fund_name AS fund_name,
    ROUND(t.return_1y * 100, 2) AS return_1y_pct,
    DATE_FORMAT(hm.report_date, '%Y-%m-%d') AS report_date,
    ROUND(hm.top10_weight_pct, 2) AS top10_weight_pct,
    ROUND(hm.hhi, 4) AS hhi
FROM top100 t
JOIN holding_metric hm
  ON hm.INNERCODE = t.INNERCODE
WHERE t.return_rank <= 100
  AND hm.top10_weight_pct <= 40
  AND hm.hhi <= 0.10
ORDER BY t.return_1y DESC, t.fund_code ASC;
