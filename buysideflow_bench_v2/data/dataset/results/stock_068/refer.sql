WITH params AS (
    SELECT
        DATE('2025-05-31') AS as_of_date,
        DATE('2023-05-31') AS base_date
),
target_index AS (
    SELECT INNERCODE, SECUABBR
    FROM (
        SELECT
            sm.INNERCODE,
            sm.SECUABBR,
            ROW_NUMBER() OVER (
                PARTITION BY sm.SECUCODE
                ORDER BY sm.INNERCODE
            ) AS rn
        FROM secumain sm
        WHERE sm.SECUCATEGORY = 4
          AND sm.SECUCODE = '399006'
    ) t
    WHERE rn = 1
),
index_constituents AS (
    SELECT DISTINCT
        ti.SECUABBR AS index_name,
        ic.SECUINNERCODE AS innercode,
        sm.COMPANYCODE
    FROM target_index ti
    JOIN lc_indexcomponent ic
      ON ic.INDEXINNERCODE = ti.INNERCODE
    JOIN secumain sm
      ON sm.INNERCODE = ic.SECUINNERCODE
    CROSS JOIN params p
    WHERE ic.INDATE <= p.as_of_date
      AND (ic.OUTDATE IS NULL OR ic.OUTDATE > p.as_of_date)
),
component_companies AS (
    SELECT DISTINCT COMPANYCODE
    FROM index_constituents
),
component_securities AS (
    SELECT DISTINCT innercode
    FROM index_constituents
),
financial_ranked AS (
    SELECT
        m.COMPANYCODE,
        m.ROETTM,
        m.ORCOMGROWRATE3Y / 100.0 AS revenue_cagr_3y,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE
            ORDER BY
                m.ENDDATE DESC,
                m.INFOPUBLDATE DESC,
                COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                m.JSID DESC
        ) AS rn
    FROM lc_mainindexnew m
    JOIN component_companies cc
      ON cc.COMPANYCODE = m.COMPANYCODE
    CROSS JOIN params p
    WHERE m.ENDDATE <= p.as_of_date
      AND m.INFOPUBLDATE <= p.as_of_date
),
latest_financial AS (
    SELECT COMPANYCODE, ROETTM, revenue_cagr_3y
    FROM financial_ranked
    WHERE rn = 1
),
industry_ranked AS (
    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYNAME,
        ROW_NUMBER() OVER (
            PARTITION BY e.COMPANYCODE
            ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
        ) AS rn
    FROM lc_exgindustry e
    JOIN component_companies cc
      ON cc.COMPANYCODE = e.COMPANYCODE
    CROSS JOIN params p
    WHERE e.STANDARD = 38
      AND e.INFOPUBLDATE <= p.as_of_date
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
),
industry_asof AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME
    FROM industry_ranked
    WHERE rn = 1
),
trade_dates AS (
    SELECT
        (
            SELECT MAX(td.TRADINGDATE)
            FROM qt_tradingdaynew td
            CROSS JOIN params p1
            WHERE td.IFTRADINGDAY = 1
              AND td.SECUMARKET = 90
              AND td.TRADINGDATE <= p1.base_date
        ) AS base_trade_date,
        (
            SELECT MAX(td.TRADINGDATE)
            FROM qt_tradingdaynew td
            CROSS JOIN params p2
            WHERE td.IFTRADINGDAY = 1
              AND td.SECUMARKET = 90
              AND td.TRADINGDATE <= p2.as_of_date
        ) AS end_trade_date
),
quote_ranked AS (
    SELECT
        q.INNERCODE,
        DATE(q.TRADINGDAY) AS trading_day,
        q.CLOSEPRICE,
        q.PREVCLOSEPRICE,
        ROW_NUMBER() OVER (
            PARTITION BY q.INNERCODE, q.TRADINGDAY
            ORDER BY q.JSID DESC
        ) AS rn
    FROM qt_dailyquote q
    JOIN component_securities cs
      ON cs.innercode = q.INNERCODE
    CROSS JOIN trade_dates td
    WHERE q.TRADINGDAY >= td.base_trade_date
      AND q.TRADINGDAY <= td.end_trade_date
),
quote_daily AS (
    SELECT INNERCODE, trading_day, CLOSEPRICE, PREVCLOSEPRICE
    FROM quote_ranked
    WHERE rn = 1
),
linked_returns AS (
    SELECT
        q.INNERCODE,
        EXP(
            SUM(
                CASE
                    WHEN q.trading_day > td.base_trade_date
                    THEN LN(q.CLOSEPRICE / q.PREVCLOSEPRICE)
                END
            )
        ) - 1 AS cumulative_return
    FROM quote_daily q
    CROSS JOIN trade_dates td
    GROUP BY q.INNERCODE
    HAVING
        MAX(
            CASE
                WHEN q.trading_day = td.base_trade_date
                 AND q.CLOSEPRICE > 0
                THEN 1 ELSE 0
            END
        ) = 1
        AND MAX(
            CASE
                WHEN q.trading_day = td.end_trade_date
                 AND q.CLOSEPRICE > 0
                THEN 1 ELSE 0
            END
        ) = 1
        AND SUM(CASE WHEN q.trading_day > td.base_trade_date THEN 1 ELSE 0 END) > 0
        AND SUM(
            CASE
                WHEN q.trading_day > td.base_trade_date
                 AND (q.CLOSEPRICE IS NULL OR q.CLOSEPRICE <= 0
                      OR q.PREVCLOSEPRICE IS NULL OR q.PREVCLOSEPRICE <= 0)
                THEN 1 ELSE 0
            END
        ) = 0
),
scored AS (
    SELECT
        ic.index_name,
        ia.FIRSTINDUSTRYNAME AS industry,
        CASE
            WHEN lf.ROETTM > 20
             AND lf.revenue_cagr_3y > 0.20
             AND lr.cumulative_return > 0.30
            THEN 1 ELSE 0
        END AS hit_flag
    FROM index_constituents ic
    LEFT JOIN latest_financial lf
      ON lf.COMPANYCODE = ic.COMPANYCODE
    LEFT JOIN linked_returns lr
      ON lr.INNERCODE = ic.innercode
    LEFT JOIN industry_asof ia
      ON ia.COMPANYCODE = ic.COMPANYCODE
),
index_summary AS (
    SELECT
        index_name,
        CAST(SUM(hit_flag) AS UNSIGNED) AS hit_count,
        COUNT(*) AS total_count,
        ROUND(SUM(hit_flag) / NULLIF(COUNT(*), 0) * 100, 2) AS hit_ratio
    FROM scored
    GROUP BY index_name
),
industry_summary AS (
    SELECT
        index_name,
        COALESCE(industry, '未分类') AS industry,
        CAST(COUNT(*) AS UNSIGNED) AS industry_hit_count
    FROM scored
    WHERE hit_flag = 1
    GROUP BY index_name, COALESCE(industry, '未分类')
)
SELECT
    i.index_name AS `指数`,
    i.hit_count AS `满足数`,
    i.total_count AS `成分股总数`,
    i.hit_ratio AS `占比`,
    COALESCE(s.industry, '无命中行业') AS `行业`,
    CAST(COALESCE(s.industry_hit_count, 0) AS UNSIGNED) AS `行业满足数`
FROM index_summary i
LEFT JOIN industry_summary s
  ON s.index_name = i.index_name
ORDER BY i.index_name, COALESCE(s.industry, '无命中行业');
