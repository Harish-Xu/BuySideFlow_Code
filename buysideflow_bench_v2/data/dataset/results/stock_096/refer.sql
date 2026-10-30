WITH
params AS (
    SELECT
        DATE('2025-12-31') AS as_of_date,
        DATE_ADD(DATE('2025-12-31'), INTERVAL 1 DAY) AS visible_before,
        DATE('2025-09-30') AS latest_report_end
),
trade_asof AS (
    SELECT MAX(td.TRADINGDATE) AS trade_day
    FROM qt_tradingdaynew td
    JOIN params p
      ON td.TRADINGDATE <= p.as_of_date
    WHERE td.IFTRADINGDAY = 1
      AND td.SECUMARKET IN (83, 90)
),
recent_trade_days AS (
    SELECT tradingday
    FROM (
        SELECT DISTINCT td.TRADINGDATE AS tradingday
        FROM qt_tradingdaynew td
        JOIN trade_asof ta
          ON td.TRADINGDATE <= ta.trade_day
        WHERE td.IFTRADINGDAY = 1
          AND td.SECUMARKET IN (83, 90)
        ORDER BY td.TRADINGDATE DESC
        LIMIT 61
    ) d
),
return_window AS (
    SELECT MIN(tradingday) AS start_day, MAX(tradingday) AS end_day, COUNT(*) AS day_count
    FROM recent_trade_days
),
list_status_asof AS (
    SELECT INNERCODE, change_type
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE AS change_type,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY
                    DATE(ls.CHANGEDATE) DESC,
                    COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
                    ls.JSID DESC
            ) AS rn
        FROM (
            SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID, SECUMARKET
            FROM lc_liststatus
            UNION ALL
            SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID, 83 AS SECUMARKET
            FROM lc_stibliststatus
        ) ls
        JOIN trade_asof ta
          ON DATE(ls.CHANGEDATE) <= ta.trade_day
        JOIN params p
          ON COALESCE(ls.INSERTTIME, ls.CHANGEDATE) < p.visible_before
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) x
    WHERE rn = 1
),
stock_raw AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        s.LISTEDSECTOR,
        ROW_NUMBER() OVER (
            PARTITION BY s.COMPANYCODE
            ORDER BY
                CASE WHEN s.LISTEDSECTOR = 1 THEN 0 ELSE 1 END,
                s.SECUCODE,
                s.INNERCODE
        ) AS rn
    FROM secumain s
    JOIN trade_asof ta
      ON s.LISTEDDATE <= ta.trade_day
    LEFT JOIN list_status_asof lsa
      ON lsa.INNERCODE = s.INNERCODE
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR IN (1, 2, 6, 7)
      AND s.COMPANYCODE IS NOT NULL
      AND lsa.change_type IN (1, 3)
),
stock_base AS (
    SELECT INNERCODE, COMPANYCODE, LISTEDSECTOR
    FROM stock_raw
    WHERE rn = 1
),
risk_alert_active AS (
    SELECT DISTINCT sb.INNERCODE
    FROM stock_base sb
    JOIN cs_riskalert ra
      ON ra.INNERCODE = sb.INNERCODE
    JOIN trade_asof ta
      ON DATE(ra.IMPLEMENTDATE) <= ta.trade_day
    JOIN params p
      ON COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) < p.visible_before
    WHERE ra.RISKALERTTYPE IN (1, 5, 12, 14)
      AND (
          ra.REMOVEDATE IS NULL
          OR DATE(ra.REMOVEDATE) > ta.trade_day
          OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) >= p.visible_before
      )
),
normal_quote AS (
    SELECT DISTINCT q.INNERCODE
    FROM qt_dailyquote q
    JOIN stock_base sb
      ON sb.INNERCODE = q.INNERCODE
    JOIN trade_asof ta
      ON q.TRADINGDAY = ta.trade_day
    WHERE q.CLOSEPRICE IS NOT NULL
      AND q.CLOSEPRICE > 0
      AND COALESCE(q.TURNOVERVALUE, 0) > 0
),
industry_union AS (
    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYCODE AS industry_code,
        e.FIRSTINDUSTRYNAME AS industry_name,
        e.INFOPUBLDATE,
        e.CANCELDATE,
        e.INSERTTIME,
        e.JSID
    FROM lc_exgindustry e
    JOIN stock_base sb
      ON sb.COMPANYCODE = e.COMPANYCODE
     AND sb.LISTEDSECTOR <> 7
    JOIN trade_asof ta
      ON e.INFOPUBLDATE < DATE_ADD(ta.trade_day, INTERVAL 1 DAY)
    WHERE e.STANDARD = 38
      AND e.FIRSTINDUSTRYCODE IS NOT NULL
      AND e.FIRSTINDUSTRYNAME IS NOT NULL
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > (SELECT trade_day FROM trade_asof))
    UNION ALL
    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYCODE,
        e.FIRSTINDUSTRYNAME,
        e.INFOPUBLDATE,
        e.CANCELDATE,
        e.INSERTTIME,
        e.JSID
    FROM lc_stibexgindustry e
    JOIN stock_base sb
      ON sb.COMPANYCODE = e.COMPANYCODE
     AND sb.LISTEDSECTOR = 7
    JOIN trade_asof ta
      ON e.EFFECTIVEDATE <= ta.trade_day
     AND e.INFOPUBLDATE < DATE_ADD(ta.trade_day, INTERVAL 1 DAY)
    WHERE e.STANDARD = 38
      AND e.FIRSTINDUSTRYCODE IS NOT NULL
      AND e.FIRSTINDUSTRYNAME IS NOT NULL
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > (SELECT trade_day FROM trade_asof))
),
industry_raw AS (
    SELECT
        u.*,
        ROW_NUMBER() OVER (
            PARTITION BY u.COMPANYCODE
            ORDER BY u.INFOPUBLDATE DESC,
                     COALESCE(u.CANCELDATE, DATE('9999-12-31')) DESC,
                     COALESCE(u.INSERTTIME, u.INFOPUBLDATE) DESC,
                     u.JSID DESC
        ) AS rn
    FROM industry_union u
),
industry_asof AS (
    SELECT COMPANYCODE, industry_code, industry_name
    FROM industry_raw
    WHERE rn = 1
),
eligible_stock AS (
    SELECT
        sb.INNERCODE,
        sb.COMPANYCODE,
        sb.LISTEDSECTOR,
        ia.industry_code,
        ia.industry_name
    FROM stock_base sb
    JOIN normal_quote nq
      ON nq.INNERCODE = sb.INNERCODE
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    LEFT JOIN risk_alert_active ra
      ON ra.INNERCODE = sb.INNERCODE
    WHERE ra.INNERCODE IS NULL
),
stock_return AS (
    SELECT
        es.COMPANYCODE,
        es.industry_code,
        es.industry_name,
        MAX(CASE WHEN q.TRADINGDAY = rw.end_day THEN q.CLOSEPRICE END)
            / NULLIF(MAX(CASE WHEN q.TRADINGDAY = rw.start_day THEN q.CLOSEPRICE END), 0) - 1 AS stock_ret_60d
    FROM eligible_stock es
    JOIN return_window rw
      ON rw.day_count = 61
    JOIN qt_dailyquote q
      ON q.INNERCODE = es.INNERCODE
     AND q.TRADINGDAY IN (rw.start_day, rw.end_day)
    WHERE q.CLOSEPRICE IS NOT NULL
      AND q.CLOSEPRICE > 0
    GROUP BY es.COMPANYCODE, es.industry_code, es.industry_name
    HAVING COUNT(DISTINCT q.TRADINGDAY) = 2
),
hs300_return AS (
    SELECT
        MAX(CASE WHEN q.TRADINGDAY = rw.end_day THEN q.CLOSEPRICE END)
            / NULLIF(MAX(CASE WHEN q.TRADINGDAY = rw.start_day THEN q.CLOSEPRICE END), 0) - 1 AS hs300_ret_60d
    FROM return_window rw
    JOIN secumain s
      ON s.SECUCATEGORY = 4
     AND s.SECUCODE = '000300'
    JOIN qt_indexquote q
      ON q.INNERCODE = s.INNERCODE
     AND q.TRADINGDAY IN (rw.start_day, rw.end_day)
    WHERE q.CLOSEPRICE IS NOT NULL
      AND q.CLOSEPRICE > 0
),
momentum_values AS (
    SELECT
        sr.industry_code,
        sr.industry_name,
        sr.stock_ret_60d - hr.hs300_ret_60d AS factor_value
    FROM stock_return sr
    CROSS JOIN hs300_return hr
    WHERE hr.hs300_ret_60d IS NOT NULL
),
momentum_ranked AS (
    SELECT
        industry_code,
        industry_name,
        factor_value,
        ROW_NUMBER() OVER (PARTITION BY industry_code ORDER BY factor_value) AS rn,
        COUNT(*) OVER (PARTITION BY industry_code) AS cnt
    FROM momentum_values
),
industry_momentum AS (
    SELECT industry_code, industry_name, AVG(factor_value) AS momentum_factor
    FROM momentum_ranked
    WHERE rn IN (FLOOR((cnt + 1) / 2), FLOOR((cnt + 2) / 2))
    GROUP BY industry_code, industry_name
),
valuation_raw AS (
    SELECT
        es.INNERCODE,
        es.industry_code,
        es.industry_name,
        v.PE,
        ROW_NUMBER() OVER (
            PARTITION BY es.INNERCODE
            ORDER BY COALESCE(v.INSERTTIME, v.TRADINGDAY) DESC, v.JSID DESC
        ) AS rn
    FROM eligible_stock es
    JOIN lc_dindicesforvaluation v
      ON v.INNERCODE = es.INNERCODE
    JOIN trade_asof ta
      ON v.TRADINGDAY = ta.trade_day
    WHERE v.PE IS NOT NULL
      AND v.PE > 0
),
value_values AS (
    SELECT industry_code, industry_name, 1.0 / PE AS factor_value
    FROM valuation_raw
    WHERE rn = 1
),
value_ranked AS (
    SELECT
        industry_code,
        industry_name,
        factor_value,
        ROW_NUMBER() OVER (PARTITION BY industry_code ORDER BY factor_value) AS rn,
        COUNT(*) OVER (PARTITION BY industry_code) AS cnt
    FROM value_values
),
industry_value AS (
    SELECT industry_code, industry_name, AVG(factor_value) AS value_factor
    FROM value_ranked
    WHERE rn IN (FLOOR((cnt + 1) / 2), FLOOR((cnt + 2) / 2))
    GROUP BY industry_code, industry_name
),
financial_union AS (
    SELECT
        es.COMPANYCODE,
        es.industry_code,
        es.industry_name,
        m.ROE,
        m.OPERATINGREVENUEGROWRATE,
        m.INFOPUBLDATE,
        m.INSERTTIME,
        m.JSID AS record_id
    FROM eligible_stock es
    JOIN lc_mainindexnew m
      ON m.COMPANYCODE = es.COMPANYCODE
     AND es.LISTEDSECTOR <> 7
    JOIN params p
      ON m.ENDDATE = p.latest_report_end
     AND m.INFOPUBLDATE < p.visible_before
    WHERE m.ROE IS NOT NULL
       OR m.OPERATINGREVENUEGROWRATE IS NOT NULL
    UNION ALL
    SELECT
        es.COMPANYCODE,
        es.industry_code,
        es.industry_name,
        m.ROE,
        m.OPERATINGREVENUEYOY,
        m.INFOPUBLDATE,
        NULL AS INSERTTIME,
        m.ID AS record_id
    FROM eligible_stock es
    JOIN lc_stibmainindex m
      ON m.COMPANYCODE = es.COMPANYCODE
     AND es.LISTEDSECTOR = 7
    JOIN params p
      ON m.ENDDATE = p.latest_report_end
     AND m.INFOPUBLDATE < p.visible_before
    WHERE m.ROE IS NOT NULL
       OR m.OPERATINGREVENUEYOY IS NOT NULL
),
financial_raw AS (
    SELECT
        f.COMPANYCODE,
        f.industry_code,
        f.industry_name,
        f.ROE,
        f.OPERATINGREVENUEGROWRATE,
        ROW_NUMBER() OVER (
            PARTITION BY f.COMPANYCODE
            ORDER BY
                f.INFOPUBLDATE DESC,
                COALESCE(f.INSERTTIME, f.INFOPUBLDATE) DESC,
                f.record_id DESC
        ) AS rn
    FROM financial_union f
),
financial_values AS (
    SELECT industry_code, industry_name, ROE AS quality_value, OPERATINGREVENUEGROWRATE AS growth_value
    FROM financial_raw
    WHERE rn = 1
),
quality_ranked AS (
    SELECT
        industry_code,
        industry_name,
        quality_value,
        ROW_NUMBER() OVER (PARTITION BY industry_code ORDER BY quality_value) AS rn,
        COUNT(*) OVER (PARTITION BY industry_code) AS cnt
    FROM financial_values
    WHERE quality_value IS NOT NULL
),
industry_quality AS (
    SELECT industry_code, industry_name, AVG(quality_value) AS quality_factor
    FROM quality_ranked
    WHERE rn IN (FLOOR((cnt + 1) / 2), FLOOR((cnt + 2) / 2))
    GROUP BY industry_code, industry_name
),
growth_ranked AS (
    SELECT
        industry_code,
        industry_name,
        growth_value,
        ROW_NUMBER() OVER (PARTITION BY industry_code ORDER BY growth_value) AS rn,
        COUNT(*) OVER (PARTITION BY industry_code) AS cnt
    FROM financial_values
    WHERE growth_value IS NOT NULL
),
industry_growth AS (
    SELECT industry_code, industry_name, AVG(growth_value) AS growth_factor
    FROM growth_ranked
    WHERE rn IN (FLOOR((cnt + 1) / 2), FLOOR((cnt + 2) / 2))
    GROUP BY industry_code, industry_name
),
industry_factors AS (
    SELECT
        im.industry_code,
        im.industry_name,
        im.momentum_factor,
        iv.value_factor,
        iq.quality_factor,
        ig.growth_factor
    FROM industry_momentum im
    JOIN industry_value iv
      ON iv.industry_code = im.industry_code
    JOIN industry_quality iq
      ON iq.industry_code = im.industry_code
    JOIN industry_growth ig
      ON ig.industry_code = im.industry_code
),
industry_zscore AS (
    SELECT
        f.*,
        COALESCE((f.momentum_factor - AVG(f.momentum_factor) OVER ()) / NULLIF(STDDEV_POP(f.momentum_factor) OVER (), 0), 0) AS momentum_z,
        COALESCE((f.value_factor - AVG(f.value_factor) OVER ()) / NULLIF(STDDEV_POP(f.value_factor) OVER (), 0), 0) AS value_z,
        COALESCE((f.quality_factor - AVG(f.quality_factor) OVER ()) / NULLIF(STDDEV_POP(f.quality_factor) OVER (), 0), 0) AS quality_z,
        COALESCE((f.growth_factor - AVG(f.growth_factor) OVER ()) / NULLIF(STDDEV_POP(f.growth_factor) OVER (), 0), 0) AS growth_z
    FROM industry_factors f
),
ranked_industry AS (
    SELECT
        industry_code,
        industry_name,
        momentum_z,
        value_z,
        quality_z,
        growth_z,
        (momentum_z + value_z + quality_z + growth_z) / 4.0 AS composite_z,
        ROW_NUMBER() OVER (
            ORDER BY (momentum_z + value_z + quality_z + growth_z) / 4.0 DESC, industry_code
        ) AS industry_rank
    FROM industry_zscore
)
SELECT
    industry_code AS `行业代码`,
    industry_name AS `行业名称`,
    ROUND(momentum_z, 2) AS `动量因子得分`,
    ROUND(value_z, 2) AS `价值因子得分`,
    ROUND(quality_z, 2) AS `质量因子得分`,
    ROUND(growth_z, 2) AS `成长因子得分`,
    ROUND(composite_z, 2) AS `综合因子得分`,
    industry_rank AS `行业排名`
FROM ranked_industry
WHERE industry_rank <= 5
ORDER BY industry_rank;
