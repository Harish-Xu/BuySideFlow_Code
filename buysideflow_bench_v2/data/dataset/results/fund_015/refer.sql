WITH params AS (
    SELECT
        DATE('2025-10-28') AS as_of_date,
        DATE('2025-10-29') AS as_of_next_day,
        DATE('2025-09-30') AS report_date
),
fund_info AS (
    SELECT
        sm.INNERCODE,
        sm.SECUCODE AS fund_code,
        sm.SECUABBR AS fund_name
    FROM secumain sm
    WHERE sm.SECUCATEGORY = 8
      AND sm.SECUCODE IN ('001484', '006567')
),
visible_key_holdings AS (
    SELECT
        fi.INNERCODE,
        fi.fund_code,
        fi.fund_name,
        DATE(ksp.REPORTDATE) AS report_date,
        ksp.STOCKINNERCODE AS stock_innercode,
        MIN(COALESCE(ksp.SERIALNUMBER, 999)) AS serial_number,
        SUM(ksp.MARKETVALUE) AS market_value
    FROM mf_keystockportfolio ksp
    JOIN fund_info fi
      ON fi.INNERCODE = ksp.INNERCODE
    JOIN params p
      ON DATE(ksp.REPORTDATE) = p.report_date
    WHERE ksp.SERIALNUMBER <= 10
      AND ksp.STOCKINNERCODE IS NOT NULL
      AND ksp.MARKETVALUE IS NOT NULL
      AND ksp.MARKETVALUE > 0
      AND ksp.INFOPUBLDATE < p.as_of_next_day
    GROUP BY
        fi.INNERCODE,
        fi.fund_code,
        fi.fund_name,
        DATE(ksp.REPORTDATE),
        ksp.STOCKINNERCODE
),
a_share_valuation AS (
    SELECT
        v.INNERCODE AS stock_innercode,
        v.PE AS pe_ttm,
        v.DIVIDENDRATIO AS dividend_yield_pct
    FROM lc_dindicesforvaluation v
    JOIN params p
      ON v.TRADINGDAY = p.report_date
    WHERE v.INSERTTIME < (SELECT as_of_next_day FROM params)
),
hk_valuation_ranked AS (
    SELECT
        q.INNERCODE AS stock_innercode,
        q.PERATIO AS pe_ttm,
        q.DIVIDENDYIELD AS dividend_yield_pct,
        ROW_NUMBER() OVER (
            PARTITION BY q.INNERCODE, q.TRADINGDAY
            ORDER BY q.XGRQ DESC, q.ID DESC
        ) AS rn
    FROM qt_hkdailyquote q
    JOIN params p
      ON q.TRADINGDAY = p.report_date
),
hk_valuation AS (
    SELECT stock_innercode, pe_ttm, dividend_yield_pct
    FROM hk_valuation_ranked
    WHERE rn = 1
),
stock_valuation AS (
    SELECT stock_innercode, pe_ttm, dividend_yield_pct
    FROM a_share_valuation
    UNION ALL
    SELECT stock_innercode, pe_ttm, dividend_yield_pct
    FROM hk_valuation
),
portfolio_metric AS (
    SELECT
        h.INNERCODE,
        h.fund_code,
        h.fund_name,
        SUM(h.market_value) AS total_holding_market_value,
        -- Negative PE contributes negative implied earnings to portfolio PE.
        SUM(CASE WHEN sv.pe_ttm <> 0 THEN h.market_value ELSE 0 END)
          / NULLIF(SUM(CASE WHEN sv.pe_ttm <> 0 THEN h.market_value / sv.pe_ttm ELSE 0 END), 0) AS weighted_pe_ttm,
        SUM(CASE WHEN sv.dividend_yield_pct IS NOT NULL THEN h.market_value * sv.dividend_yield_pct ELSE 0 END)
          / NULLIF(SUM(CASE WHEN sv.dividend_yield_pct IS NOT NULL THEN h.market_value ELSE 0 END), 0) AS weighted_dividend_yield_pct
    FROM visible_key_holdings h
    LEFT JOIN stock_valuation sv
      ON sv.stock_innercode = h.stock_innercode
    GROUP BY
        h.INNERCODE,
        h.fund_code,
        h.fund_name
)
SELECT
    pm.fund_code AS `基金代码`,
    pm.fund_name AS `基金简称`,
    ROUND(pm.total_holding_market_value, 2) AS `前十大重仓股合计持仓市值`,
    ROUND(pm.weighted_pe_ttm, 2) AS `组合加权 PE(TTM)`,
    ROUND(pm.weighted_dividend_yield_pct, 2) AS `组合加权股息率`
FROM portfolio_metric pm
ORDER BY pm.fund_code ASC;
