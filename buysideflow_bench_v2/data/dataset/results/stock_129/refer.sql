WITH
params AS (
    SELECT
        DATE('2025-09-30') AS as_of_date,
        DATE('2025-06-30') AS report_end_date,
        DATE('2024-06-30') AS prior_report_end_date
),
industry_info AS (
    SELECT
        libi.INDEXCODE AS index_innercode,
        sm.SECUCODE AS industry_code,
        sm.SECUABBR AS industry_name
    FROM lc_indexbasicinfo libi
    JOIN secumain sm
      ON sm.INNERCODE = libi.INDEXCODE
    WHERE libi.PUBINDEXTYPE = 121101
      AND libi.INDUSTRYSTANDARD = 38
      AND sm.SECUCODE IS NOT NULL
),
financial_ranked AS (
    SELECT
        ii.industry_code,
        ii.industry_name,
        DATE(fi.ENDDATE) AS report_end_date,
        fi.OPERATINGREVENUEYOY AS revenue_yoy,
        fi.NPPARENTCOMOWNERSYOY AS np_parent_yoy,
        fi.ROE,
        fi.NETOPERATECASHFLOWYOY AS ocf_yoy,
        ROW_NUMBER() OVER (
            PARTITION BY ii.index_innercode, DATE(fi.ENDDATE)
            ORDER BY fi.INFOPUBLDATE DESC, fi.INSERTTIME DESC, fi.JSID DESC, fi.ID DESC
        ) AS rn
    FROM index_finindicator fi
    JOIN industry_info ii
      ON ii.index_innercode = fi.INDEXCODE
    JOIN params p
      ON DATE(fi.ENDDATE) IN (p.report_end_date, p.prior_report_end_date)
     AND fi.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
),
financial_current AS (
    SELECT
        industry_code,
        industry_name,
        MAX(CASE WHEN report_end_date = DATE('2025-06-30') THEN revenue_yoy END) AS revenue_yoy,
        MAX(CASE WHEN report_end_date = DATE('2025-06-30') THEN np_parent_yoy END) AS np_parent_yoy,
        MAX(CASE WHEN report_end_date = DATE('2025-06-30') THEN ROE END)
            - MAX(CASE WHEN report_end_date = DATE('2024-06-30') THEN ROE END) AS roe_yoy_change,
        MAX(CASE WHEN report_end_date = DATE('2025-06-30') THEN ocf_yoy END) AS ocf_yoy
    FROM financial_ranked
    WHERE rn = 1
    GROUP BY industry_code, industry_name
),
revenue_pct AS (
    SELECT
        industry_code,
        PERCENT_RANK() OVER (ORDER BY revenue_yoy) AS revenue_pct
    FROM financial_current
    WHERE revenue_yoy IS NOT NULL
),
profit_pct AS (
    SELECT
        industry_code,
        PERCENT_RANK() OVER (ORDER BY np_parent_yoy) AS profit_pct
    FROM financial_current
    WHERE np_parent_yoy IS NOT NULL
),
roe_pct AS (
    SELECT
        industry_code,
        PERCENT_RANK() OVER (ORDER BY roe_yoy_change) AS roe_pct
    FROM financial_current
    WHERE roe_yoy_change IS NOT NULL
),
ocf_pct AS (
    SELECT
        industry_code,
        PERCENT_RANK() OVER (ORDER BY ocf_yoy) AS ocf_pct
    FROM financial_current
    WHERE ocf_yoy IS NOT NULL
),
prosperity AS (
    SELECT
        fc.industry_code,
        fc.industry_name,
        (
            COALESCE(r.revenue_pct, 0) +
            COALESCE(p.profit_pct, 0) +
            COALESCE(roe.roe_pct, 0) +
            COALESCE(o.ocf_pct, 0)
        ) / NULLIF(
            (r.revenue_pct IS NOT NULL) +
            (p.profit_pct IS NOT NULL) +
            (roe.roe_pct IS NOT NULL) +
            (o.ocf_pct IS NOT NULL),
            0
        ) AS prosperity_score,
        (r.revenue_pct IS NOT NULL) +
        (p.profit_pct IS NOT NULL) +
        (roe.roe_pct IS NOT NULL) +
        (o.ocf_pct IS NOT NULL) AS available_items
    FROM financial_current fc
    LEFT JOIN revenue_pct r
      ON r.industry_code = fc.industry_code
    LEFT JOIN profit_pct p
      ON p.industry_code = fc.industry_code
    LEFT JOIN roe_pct roe
      ON roe.industry_code = fc.industry_code
    LEFT JOIN ocf_pct o
      ON o.industry_code = fc.industry_code
),
latest_trade AS (
    SELECT MAX(idv.TRADINGDAY) AS trading_day
    FROM lc_indexderivative idv
    JOIN industry_info ii
      ON ii.index_innercode = idv.INDEXCODE
    JOIN params p
      ON DATE(idv.TRADINGDAY) <= p.as_of_date
    WHERE idv.PE_TTM > 0
      AND idv.PB_LF > 0
),
valuation_history AS (
    SELECT
        ii.industry_code,
        ii.industry_name,
        DATE(idv.TRADINGDAY) AS trading_day,
        idv.PE_TTM,
        idv.PB_LF
    FROM lc_indexderivative idv
    JOIN industry_info ii
      ON ii.index_innercode = idv.INDEXCODE
    JOIN latest_trade lt
      ON DATE(idv.TRADINGDAY) <= DATE(lt.trading_day)
     AND DATE(idv.TRADINGDAY) >= DATE_SUB(DATE(lt.trading_day), INTERVAL 3 YEAR)
    WHERE idv.PE_TTM > 0
      AND idv.PB_LF > 0
),
current_valuation AS (
    SELECT
        vh.industry_code,
        vh.industry_name,
        vh.PE_TTM,
        vh.PB_LF
    FROM valuation_history vh
    JOIN latest_trade lt
      ON vh.trading_day = DATE(lt.trading_day)
),
valuation AS (
    SELECT
        cv.industry_code,
        cv.industry_name,
        (
            SUM(CASE WHEN vh.PE_TTM <= cv.PE_TTM THEN 1 ELSE 0 END) * 1.0 / COUNT(*) +
            SUM(CASE WHEN vh.PB_LF <= cv.PB_LF THEN 1 ELSE 0 END) * 1.0 / COUNT(*)
        ) / 2.0 AS valuation_score
    FROM current_valuation cv
    JOIN valuation_history vh
      ON vh.industry_code = cv.industry_code
    GROUP BY cv.industry_code, cv.industry_name, cv.PE_TTM, cv.PB_LF
),
joined AS (
    SELECT
        pr.industry_code,
        pr.industry_name,
        pr.prosperity_score,
        val.valuation_score
    FROM prosperity pr
    JOIN valuation val
      ON val.industry_code = pr.industry_code
    WHERE pr.available_items >= 3
),
prosperity_ordered AS (
    SELECT
        prosperity_score,
        ROW_NUMBER() OVER (ORDER BY prosperity_score) AS rn,
        COUNT(*) OVER () AS cnt
    FROM joined
),
valuation_ordered AS (
    SELECT
        valuation_score,
        ROW_NUMBER() OVER (ORDER BY valuation_score) AS rn,
        COUNT(*) OVER () AS cnt
    FROM joined
),
medians AS (
    SELECT
        (
            SELECT AVG(prosperity_score)
            FROM prosperity_ordered
            WHERE rn IN (FLOOR((cnt + 1) / 2), FLOOR((cnt + 2) / 2))
        ) AS prosperity_median,
        (
            SELECT AVG(valuation_score)
            FROM valuation_ordered
            WHERE rn IN (FLOOR((cnt + 1) / 2), FLOOR((cnt + 2) / 2))
        ) AS valuation_median
)
SELECT
    (SELECT as_of_date FROM params) AS `观察日`,
    j.industry_code AS `行业代码`,
    j.industry_name AS `行业名称`,
    ROUND(j.prosperity_score, 4) AS `景气得分`,
    ROUND(j.valuation_score, 4) AS `估值得分`,
    CASE
        WHEN j.prosperity_score > m.prosperity_median
         AND j.valuation_score > m.valuation_median THEN '高景气高估值'
        WHEN j.prosperity_score > m.prosperity_median
         AND j.valuation_score <= m.valuation_median THEN '高景气低估值'
        WHEN j.prosperity_score <= m.prosperity_median
         AND j.valuation_score > m.valuation_median THEN '低景气高估值'
        ELSE '低景气低估值'
    END AS `四象限分类`
FROM joined j
CROSS JOIN medians m
ORDER BY j.industry_code;
