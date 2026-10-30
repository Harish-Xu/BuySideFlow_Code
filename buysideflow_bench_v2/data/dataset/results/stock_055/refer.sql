WITH
params AS (
    SELECT
        DATE('2025-04-30') AS as_of_date,
        DATE('2024-12-31') AS latest_annual_end
),
target_index AS (
    SELECT s.INNERCODE
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE = '000300'
    ORDER BY s.INNERCODE
    LIMIT 1
),
hs300_constituents AS (
    SELECT DISTINCT
        ic.SECUINNERCODE AS innercode,
        sm.COMPANYCODE,
        sm.SECUCODE,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, sm.SECUCODE) AS company
    FROM lc_indexcomponent ic
    JOIN target_index ti
      ON ti.INNERCODE = ic.INDEXINNERCODE
    JOIN params p
      ON ic.INDATE <= p.as_of_date
    JOIN secumain sm
      ON sm.INNERCODE = ic.SECUINNERCODE
    WHERE (ic.OUTDATE IS NULL OR ic.OUTDATE > p.as_of_date)
      AND sm.SECUCATEGORY = 1
),
trade_asof AS (
    SELECT MAX(t.TRADINGDATE) AS trade_date
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET IN (83, 90)
),
valuation_src AS (  -- 科创板估值在 lc_stibdindiforvalue，主板表无科创板
    SELECT INNERCODE, TRADINGDAY, DIVIDENDRATIO, JSID FROM lc_dindicesforvaluation
    WHERE TRADINGDAY = (SELECT trade_date FROM trade_asof)
    UNION ALL
    SELECT INNERCODE, TRADINGDAY, DIVIDENDRATIO, JSID FROM lc_stibdindiforvalue
    WHERE TRADINGDAY = (SELECT trade_date FROM trade_asof)
),
valuation_asof AS (
    SELECT
        v.INNERCODE,
        v.DIVIDENDRATIO AS dividend_yield
    FROM valuation_src v
    JOIN (
        SELECT
            v.INNERCODE,
            MAX(v.JSID) AS max_jsid
        FROM valuation_src v
        JOIN trade_asof ta
          ON v.TRADINGDAY = ta.trade_date
        JOIN hs300_constituents c
          ON c.innercode = v.INNERCODE
        WHERE v.DIVIDENDRATIO IS NOT NULL
        GROUP BY v.INNERCODE
    ) k
      ON k.INNERCODE = v.INNERCODE
     AND k.max_jsid = v.JSID
),
top100_dividend AS (
    SELECT
        t.COMPANYCODE,
        t.SECUCODE,
        t.company,
        t.dividend_yield
    FROM (
        SELECT
            c.COMPANYCODE,
            c.SECUCODE,
            c.company,
            v.dividend_yield,
            ROW_NUMBER() OVER (
                ORDER BY v.dividend_yield DESC, c.SECUCODE
            ) AS div_rank
        FROM hs300_constituents c
        JOIN valuation_asof v
          ON v.INNERCODE = c.innercode
    ) t
    WHERE t.div_rank <= 100
),
annual_maindata AS (
    SELECT
        COMPANYCODE,
        YEAR(ENDDATE) AS report_year,
        NETOPERATECASHFLOW,
        NPFROMPARENTCOMPANYOWNERS
    FROM (
        SELECT
            m.COMPANYCODE,
            m.ENDDATE,
            m.NETOPERATECASHFLOW,
            m.NPFROMPARENTCOMPANYOWNERS,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE, m.ENDDATE
                ORDER BY CASE m.MARK
                             WHEN 1 THEN 0 WHEN 8 THEN 1 WHEN 7 THEN 2
                             WHEN 6 THEN 3 WHEN 2 THEN 4 ELSE 9
                         END,
                         m.INFOPUBLDATE DESC,
                         COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                         m.JSID DESC
            ) AS rn
        FROM (  -- 科创板财务在 lc_stibmaindata（IFADJUSTED 与 MARK 同码，取合并报表）
            SELECT COMPANYCODE, ENDDATE, NETOPERATECASHFLOW, NPFROMPARENTCOMPANYOWNERS, MARK, INFOPUBLDATE,
                   INSERTTIME, JSID, ACCOUNTINGSTANDARDS
            FROM lc_maindatanew
            WHERE ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
              AND INFOPUBLDATE < DATE('2025-05-01')
            UNION ALL
            SELECT COMPANYCODE, ENDDATE, NETOPERATECASHFLOW, NPPARCOMOWNERS, IFADJUSTED, INFOPUBLDATE,
                   INSERTTIME, JSID, 1
            FROM lc_stibmaindata
            WHERE IFMERGED = 1 AND ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
              AND INFOPUBLDATE < DATE('2025-05-01')
        ) m
        JOIN top100_dividend td
          ON td.COMPANYCODE = m.COMPANYCODE
        JOIN params p
          ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE m.MARK IN (1, 8, 7, 6, 2)
          AND m.ACCOUNTINGSTANDARDS = 1
          AND m.ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
          AND m.NETOPERATECASHFLOW IS NOT NULL
          AND m.NPFROMPARENTCOMPANYOWNERS IS NOT NULL
    ) t
    WHERE rn = 1
),
quality_stats AS (
    SELECT
        md.COMPANYCODE,
        MAX(CASE WHEN md.report_year = 2024 THEN md.NETOPERATECASHFLOW / NULLIF(md.NPFROMPARENTCOMPANYOWNERS, 0) END) AS cfo_np_2024,
        MAX(CASE WHEN md.report_year = 2023 THEN md.NETOPERATECASHFLOW / NULLIF(md.NPFROMPARENTCOMPANYOWNERS, 0) END) AS cfo_np_2023,
        MAX(CASE WHEN md.report_year = 2022 THEN md.NETOPERATECASHFLOW / NULLIF(md.NPFROMPARENTCOMPANYOWNERS, 0) END) AS cfo_np_2022,
        SUM(
            CASE
                WHEN md.NPFROMPARENTCOMPANYOWNERS > 0
                 AND md.NETOPERATECASHFLOW > md.NPFROMPARENTCOMPANYOWNERS
                THEN 1 ELSE 0
            END
        ) AS cash_cover_years
    FROM annual_maindata md
    GROUP BY md.COMPANYCODE
    HAVING COUNT(*) = 3
),
annual_roe_key AS (
    SELECT
        m.COMPANYCODE,
        m.ENDDATE,
        MAX(m.JSID) AS max_jsid
    FROM (  -- 科创板财务指标在 lc_stibmainindex（取合并报表）
        SELECT COMPANYCODE, ENDDATE, INFOPUBLDATE, INSERTTIME, JSID, ROEAVG FROM lc_mainindexnew
        WHERE ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
          AND INFOPUBLDATE < DATE('2025-05-01')
        UNION ALL
        SELECT COMPANYCODE, ENDDATE, INFOPUBLDATE, INSERTTIME, JSID, ROEAVG FROM lc_stibmainindex
        WHERE IFMERGED = 1 AND ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
          AND INFOPUBLDATE < DATE('2025-05-01')
    ) m
    JOIN top100_dividend td
      ON td.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
     AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE m.ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
      AND MONTH(m.ENDDATE) = 12
      AND m.ROEAVG IS NOT NULL
    GROUP BY m.COMPANYCODE, m.ENDDATE
),
annual_roe AS (
    SELECT
        m.COMPANYCODE,
        YEAR(m.ENDDATE) AS report_year,
        m.ROEAVG
    FROM (  -- 科创板财务指标在 lc_stibmainindex（取合并报表）
        SELECT COMPANYCODE, ENDDATE, INFOPUBLDATE, INSERTTIME, JSID, ROEAVG FROM lc_mainindexnew
        WHERE ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
          AND INFOPUBLDATE < DATE('2025-05-01')
        UNION ALL
        SELECT COMPANYCODE, ENDDATE, INFOPUBLDATE, INSERTTIME, JSID, ROEAVG FROM lc_stibmainindex
        WHERE IFMERGED = 1 AND ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
          AND INFOPUBLDATE < DATE('2025-05-01')
    ) m
    JOIN annual_roe_key k
      ON k.COMPANYCODE = m.COMPANYCODE
     AND k.ENDDATE = m.ENDDATE
     AND k.max_jsid = m.JSID
),
roe_3y AS (
    SELECT
        COMPANYCODE,
        AVG(ROEAVG) AS avg_roe_3y
    FROM annual_roe
    GROUP BY COMPANYCODE
    HAVING COUNT(*) = 3
),
classified AS (
    SELECT
        td.SECUCODE AS code,
        td.company,
        qs.cfo_np_2024,
        qs.cfo_np_2023,
        qs.cfo_np_2022,
        r3.avg_roe_3y,
        td.dividend_yield,
        CASE
            WHEN qs.cash_cover_years = 3
             AND r3.avg_roe_3y > 15
            THEN '现金股息共振型'
            WHEN qs.cash_cover_years = 3
             AND r3.avg_roe_3y <= 15
            THEN '现金覆盖但回报偏弱型'
            ELSE '账面利润主导型'
        END AS dividend_quality_bucket
    FROM top100_dividend td
    JOIN quality_stats qs
      ON qs.COMPANYCODE = td.COMPANYCODE
    JOIN roe_3y r3
      ON r3.COMPANYCODE = td.COMPANYCODE
),
high_dividend_pool_count AS (
    SELECT COUNT(*) AS pool_count
    FROM top100_dividend
),
ranked AS (
    SELECT
        c.*,
        COUNT(*) OVER (PARTITION BY c.dividend_quality_bucket) AS sample_count,
        ROUND(
            COUNT(*) OVER (PARTITION BY c.dividend_quality_bucket)
            / NULLIF(pc.pool_count, 0) * 100,
            2
        ) AS ratio_pct,
        ROW_NUMBER() OVER (
            PARTITION BY c.dividend_quality_bucket
            ORDER BY c.dividend_yield DESC, c.code
        ) AS bucket_rank
    FROM classified c
    JOIN high_dividend_pool_count pc
      ON 1 = 1
)
SELECT
    r.dividend_quality_bucket AS `分红质量类型`,
    r.sample_count AS `样本数`,
    r.ratio_pct AS `占高股息池比例(%)`,
    r.code AS `代码`,
    r.company AS `公司`,
    ROUND(r.cfo_np_2024, 4) AS `cfo_np_2024`,
    ROUND(r.cfo_np_2023, 4) AS `cfo_np_2023`,
    ROUND(r.cfo_np_2022, 4) AS `cfo_np_2022`,
    ROUND(r.avg_roe_3y, 4) AS `三年平均ROE`,
    ROUND(r.dividend_yield, 4) AS `最新股息率`
FROM ranked r
WHERE r.bucket_rank <= 10
ORDER BY FIELD(r.dividend_quality_bucket, '现金股息共振型', '现金覆盖但回报偏弱型', '账面利润主导型'),
         `最新股息率` DESC,
         `代码`;
