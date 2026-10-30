WITH params AS (
    SELECT
        DATE('2025-12-31') AS as_of_date,
        DATE('2025-09-30') AS latest_end
),
target_index AS (
    SELECT s.INNERCODE
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE = '399006'
    ORDER BY s.INNERCODE
    LIMIT 1
),
trade_asof AS (
    SELECT MAX(t.TRADINGDATE) AS trade_date
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET = 90
),
index_stocks AS (
    SELECT DISTINCT
        ic.SECUINNERCODE,
        sm.SECUCODE,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, sm.SECUCODE) AS stock_name,
        sm.COMPANYCODE,
        sm.LISTEDDATE
    FROM lc_indexcomponent ic
    JOIN target_index ti
      ON ti.INNERCODE = ic.INDEXINNERCODE
    JOIN secumain sm
      ON sm.INNERCODE = ic.SECUINNERCODE
    JOIN params p
      ON ic.INDATE <= p.as_of_date
    WHERE (ic.OUTDATE IS NULL OR ic.OUTDATE > p.as_of_date)
      AND sm.LISTEDDATE <= DATE_SUB(p.as_of_date, INTERVAL 1 YEAR)
      AND NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = sm.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND DATE(ra.IMPLEMENTDATE) <= p.as_of_date
            AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE)
                < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
            AND (
                ra.REMOVEDATE IS NULL
                OR DATE(ra.REMOVEDATE) > p.as_of_date
                OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE)
                    >= DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
            )
      )
),
latest_quarter_data AS (
    SELECT
        t.SECUINNERCODE,
        t.SECUCODE,
        t.stock_name,
        t.COMPANYCODE,
        t.NETPROFITGROWRATE
    FROM (
        SELECT
            i.SECUINNERCODE,
            i.SECUCODE,
            i.stock_name,
            i.COMPANYCODE,
            m.NETPROFITGROWRATE,
            ROW_NUMBER() OVER (
                PARTITION BY i.COMPANYCODE
                ORDER BY CASE m.MARK
                             WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                             WHEN 6 THEN 4 WHEN 2 THEN 5 ELSE 9
                         END,
                         m.INFOPUBLDATE DESC,
                         COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                         m.JSID DESC,
                         m.ID DESC
            ) AS rn
        FROM index_stocks i
        JOIN lc_qfinancialindexnew m
          ON m.COMPANYCODE = i.COMPANYCODE
        JOIN params p
          ON m.ENDDATE = p.latest_end
         AND m.INFOPUBLDATE <= p.as_of_date
        WHERE m.NETPROFITGROWRATE IS NOT NULL
          AND m.MARK IN (1, 8, 7, 6, 2)
    ) t
    WHERE t.rn = 1
),
top_growth AS (
    SELECT
        l.*,
        PERCENT_RANK() OVER (ORDER BY l.NETPROFITGROWRATE DESC) AS growth_pct
    FROM latest_quarter_data l
),
valuation_asof AS (
    SELECT
        t.INNERCODE,
        t.PE,
        t.NEGOTIABLEMV
    FROM (
        SELECT
            d.INNERCODE,
            d.PE,
            d.NEGOTIABLEMV,
            ROW_NUMBER() OVER (
                PARTITION BY d.INNERCODE
                ORDER BY d.JSID DESC, d.ID DESC
            ) AS rn
        FROM lc_dindicesforvaluation d
        JOIN index_stocks i
          ON i.SECUINNERCODE = d.INNERCODE
        JOIN trade_asof ta
          ON d.TRADINGDAY = ta.trade_date
        WHERE d.PE IS NOT NULL
          AND d.PE > 0
          AND d.NEGOTIABLEMV IS NOT NULL
    ) t
    WHERE t.rn = 1
),
pe_filtered AS (
    SELECT
        tg.SECUCODE,
        tg.stock_name,
        tg.NETPROFITGROWRATE AS netprofit_yoy_growth_rate,
        v.PE,
        v.NEGOTIABLEMV,
        PERCENT_RANK() OVER (ORDER BY v.PE ASC) AS pe_pct
    FROM top_growth tg
    JOIN valuation_asof v
      ON v.INNERCODE = tg.SECUINNERCODE
    WHERE tg.growth_pct <= 0.2
)
SELECT
    SECUCODE AS `股票代码`,
    stock_name AS `股票简称`,
    ROUND(netprofit_yoy_growth_rate, 2) AS `单季归母净利润同比增速`,
    ROUND(PE, 2) AS `PE(TTM)`,
    ROUND(NEGOTIABLEMV / 100000000, 2) AS `流通市值(亿元)`
FROM pe_filtered
WHERE pe_pct <= 0.5
ORDER BY NEGOTIABLEMV ASC, `股票代码`;
