WITH
params AS (
    SELECT DATE('2025-12-01') AS as_of_date
),
obs_trade AS (
    SELECT MAX(d.TRADINGDATE) AS trading_day
    FROM (
        SELECT DISTINCT t.TRADINGDATE
        FROM qt_tradingdaynew t
        JOIN params p
          ON t.TRADINGDATE <= p.as_of_date
        WHERE t.IFTRADINGDAY = 1
          AND t.SECUMARKET IN (83, 90)
    ) d
),
index_info AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '399317'
    ORDER BY INNERCODE
    LIMIT 1
),
constituents AS (
    SELECT
        sm.INNERCODE,
        sm.COMPANYCODE,
        sm.SECUCODE,
        COALESCE(sm.CHINAMEABBR, sm.SECUABBR, sm.CHINAME, sm.SECUCODE) AS stock_name
    FROM lc_indexcomponent ic
    JOIN index_info ii
      ON ii.INNERCODE = ic.INDEXINNERCODE
    JOIN obs_trade ot
      ON ic.INDATE <= ot.trading_day
    JOIN secumain sm
      ON sm.INNERCODE = ic.SECUINNERCODE
    WHERE (ic.OUTDATE IS NULL OR ic.OUTDATE > ot.trading_day)
      AND sm.SECUCATEGORY = 1
      AND sm.SECUMARKET IN (83, 90)
      AND NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = sm.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND ra.IMPLEMENTDATE <= ot.trading_day
            AND COALESCE(ra.IMPLANNOUCEDATE, ra.IMPLEMENTDATE) < DATE_ADD(ot.trading_day, INTERVAL 1 DAY)
            AND (
                ra.REMOVEDATE IS NULL
                OR ra.REMOVEDATE > ot.trading_day
                OR ra.REMOVEINFOPUBLDATE IS NULL
                OR ra.REMOVEINFOPUBLDATE >= DATE_ADD(ot.trading_day, INTERVAL 1 DAY)
            )
      )
),
valuation_raw AS (
    SELECT
        d.INNERCODE,
        d.PB,
        d.TOTALMV,
        ROW_NUMBER() OVER (
            PARTITION BY d.INNERCODE
            ORDER BY d.JSID DESC
        ) AS rn
    FROM (  -- 科创板估值在 lc_stibdindiforvalue
        SELECT INNERCODE, TRADINGDAY, PB, TOTALMV, JSID FROM lc_dindicesforvaluation
        WHERE TRADINGDAY = (SELECT trading_day FROM obs_trade)
        UNION ALL
        SELECT INNERCODE, TRADINGDAY, PB, TOTALMV, JSID FROM lc_stibdindiforvalue
        WHERE TRADINGDAY = (SELECT trading_day FROM obs_trade)
    ) d
    JOIN constituents c
      ON c.INNERCODE = d.INNERCODE
    JOIN obs_trade ot
      ON d.TRADINGDAY = ot.trading_day
    WHERE d.TOTALMV IS NOT NULL
      AND d.TOTALMV > 0
),
valuation AS (
    SELECT
        INNERCODE,
        PB,
        TOTALMV
    FROM valuation_raw
    WHERE rn = 1
),
mv_ranked AS (
    SELECT
        c.INNERCODE,
        c.COMPANYCODE,
        c.SECUCODE,
        c.stock_name,
        v.PB,
        v.TOTALMV,
        ROW_NUMBER() OVER (ORDER BY v.TOTALMV ASC, c.SECUCODE) AS mv_rank,
        COUNT(*) OVER () AS total_cnt
    FROM constituents c
    JOIN valuation v
      ON v.INNERCODE = c.INNERCODE
),
smallest_10pct AS (
    SELECT *
    FROM mv_ranked
    WHERE mv_rank <= CEIL(total_cnt * 0.1)
),
latest_financial_raw AS (
    SELECT
        m.COMPANYCODE,
        m.ROE,
        m.NETOPERATECASHFLOW / NULLIF(m.OPERATINGREENUE, 0) * 100 AS ocf_to_revenue,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE
            ORDER BY
                m.ENDDATE DESC,
                CASE m.MARK
                    WHEN 1 THEN 1
                    WHEN 8 THEN 2
                    WHEN 7 THEN 3
                    WHEN 6 THEN 4
                    WHEN 2 THEN 5
                    ELSE 99
                END,
                m.INFOPUBLDATE DESC,
                m.ID DESC
        ) AS rn
    FROM (  -- 科创板财务在 lc_stibmaindata（IFADJUSTED 与 MARK 同码，取合并报表）
        SELECT ID, COMPANYCODE, ENDDATE, INFOPUBLDATE, MARK, ROE, NETOPERATECASHFLOW, OPERATINGREENUE
        FROM lc_maindatanew WHERE ENDDATE <= DATE('2025-09-30') AND INFOPUBLDATE <= DATE('2025-12-01')
        UNION ALL
        SELECT ID, COMPANYCODE, ENDDATE, INFOPUBLDATE, IFADJUSTED, ROE, NETOPERATECASHFLOW, OPERATINGREENUE
        FROM lc_stibmaindata WHERE IFMERGED = 1 AND ENDDATE <= DATE('2025-09-30') AND INFOPUBLDATE <= DATE('2025-12-01')
    ) m
    JOIN smallest_10pct s
      ON s.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.INFOPUBLDATE <= p.as_of_date
    WHERE m.ENDDATE <= DATE('2025-09-30')
),
latest_financial AS (
    SELECT
        COMPANYCODE,
        ROE,
        ocf_to_revenue
    FROM latest_financial_raw
    WHERE rn = 1
),
filtered AS (
    SELECT
        s.SECUCODE,
        s.stock_name,
        s.PB,
        m.ROE,
        m.ocf_to_revenue,
        s.TOTALMV
    FROM smallest_10pct s
    JOIN latest_financial m
      ON m.COMPANYCODE = s.COMPANYCODE
    WHERE s.PB > 0
      AND m.ROE > 0
      AND m.ocf_to_revenue > 0
)
SELECT
    SECUCODE AS `股票代码`,
    stock_name AS `股票简称`,
    ROUND(PB, 4) AS `市净率`,
    ROUND(ROE, 4) AS `净资产收益率`,
    ROUND(ocf_to_revenue, 4) AS `经营现金流/营业收入`,
    ROUND(TOTALMV, 2) AS `总市值`
FROM filtered
ORDER BY TOTALMV ASC, SECUCODE
LIMIT 30;
