WITH params AS (
    SELECT DATE('2026-01-05') AS as_of_date
),
list_status_asof AS (
    SELECT INNERCODE
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY
                    ls.CHANGEDATE DESC,
                    COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
                    ls.JSID DESC,
                    ls.ID DESC
            ) AS rn
        FROM lc_liststatus ls
        JOIN secumain sx
          ON sx.INNERCODE = ls.INNERCODE
         AND sx.SECUCATEGORY = 1
         AND sx.SECUMARKET IN (83, 90)
         AND sx.LISTEDSECTOR = 6
        JOIN params p
          ON ls.CHANGEDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
      AND CHANGETYPE IN (1, 3)
),
stock_base AS (
    SELECT COMPANYCODE, SECUCODE, stock_name
    FROM (
        SELECT
            s.COMPANYCODE,
            s.SECUCODE,
            COALESCE(NULLIF(s.SECUABBR, ''), NULLIF(s.CHINAMEABBR, ''), s.CHINAME) AS stock_name,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.SECUCODE
            ) AS rn
        FROM secumain s
        JOIN list_status_asof ls
          ON ls.INNERCODE = s.INNERCODE
        JOIN params p
          ON DATE(s.LISTEDDATE) <= p.as_of_date
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR = 6
          AND s.COMPANYCODE IS NOT NULL
    ) t
    WHERE rn = 1
),
main_index_ranked AS (
    SELECT
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS report_date,
        m.OPERATINGEXPENSERATETTM
            + m.ADMINIEXPENSERATETTM
            + m.FINANCIALEXPENSERATETTM AS fee_rate_ttm,
        m.NETPROFITRATIOTTM AS net_margin_ttm,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
            ORDER BY
                m.INFOPUBLDATE DESC,
                COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                m.JSID DESC,
                m.ID DESC
        ) AS rn
    FROM lc_mainindexnew m
    JOIN stock_base b
      ON b.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.ENDDATE >= DATE_SUB(p.as_of_date, INTERVAL 4 YEAR)
     AND m.ENDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
     AND m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
),
revenue_ranked AS (
    SELECT
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS report_date,
        m.OPERATINGREENUE,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
            ORDER BY
                CASE m.MARK
                    WHEN 1 THEN 1
                    WHEN 8 THEN 2
                    WHEN 7 THEN 3
                    WHEN 6 THEN 4
                    WHEN 2 THEN 5
                    ELSE 99
                END,
                m.INFOPUBLDATE DESC,
                COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                m.JSID DESC,
                m.ID DESC
        ) AS rn
    FROM lc_maindatanew m
    JOIN stock_base b
      ON b.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.ENDDATE >= DATE_SUB(p.as_of_date, INTERVAL 4 YEAR)
     AND m.ENDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
     AND m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE m.MARK IN (1, 8, 7, 6, 2)
      AND m.OPERATINGREENUE IS NOT NULL
),
revenue_periods AS (
    SELECT COMPANYCODE, report_date, OPERATINGREENUE
    FROM revenue_ranked
    WHERE rn = 1
),
period_data AS (
    SELECT
        s.COMPANYCODE,
        s.report_date,
        s.fee_rate_ttm,
        s.net_margin_ttm
    FROM main_index_ranked s
    WHERE s.rn = 1
),
latest_period AS (
    SELECT COMPANYCODE, MAX(report_date) AS report_date
    FROM period_data
    GROUP BY COMPANYCODE
),
revenue_ttm AS (
    SELECT
        cur.COMPANYCODE,
        cur.report_date,
        CASE
            WHEN MONTH(cur.report_date) = 12 THEN cur.OPERATINGREENUE
            ELSE cur.OPERATINGREENUE
               + annual.OPERATINGREENUE
               - prior_same.OPERATINGREENUE
        END AS revenue_ttm
    FROM revenue_periods cur
    LEFT JOIN revenue_periods annual
      ON annual.COMPANYCODE = cur.COMPANYCODE
     AND annual.report_date = STR_TO_DATE(
            CONCAT(YEAR(cur.report_date) - 1, '-12-31'),
            '%Y-%m-%d'
         )
    LEFT JOIN revenue_periods prior_same
      ON prior_same.COMPANYCODE = cur.COMPANYCODE
     AND prior_same.report_date = DATE_SUB(cur.report_date, INTERVAL 1 YEAR)
),
screened AS (
    SELECT
        b.SECUCODE,
        b.stock_name,
        r.revenue_ttm,
        cur.fee_rate_ttm,
        prev.fee_rate_ttm - cur.fee_rate_ttm AS fee_rate_drop,
        cur.net_margin_ttm - prev.net_margin_ttm AS net_margin_change
    FROM latest_period lp
    JOIN period_data cur
      ON cur.COMPANYCODE = lp.COMPANYCODE
     AND cur.report_date = lp.report_date
    JOIN period_data prev
      ON prev.COMPANYCODE = lp.COMPANYCODE
     AND prev.report_date = DATE_SUB(lp.report_date, INTERVAL 2 YEAR)
    JOIN revenue_ttm r
      ON r.COMPANYCODE = lp.COMPANYCODE
     AND r.report_date = lp.report_date
    JOIN stock_base b
      ON b.COMPANYCODE = lp.COMPANYCODE
    WHERE r.revenue_ttm >= 1000000000
      AND cur.fee_rate_ttm IS NOT NULL
      AND prev.fee_rate_ttm IS NOT NULL
      AND cur.net_margin_ttm IS NOT NULL
      AND prev.net_margin_ttm IS NOT NULL
      AND prev.fee_rate_ttm - cur.fee_rate_ttm > 3
)
SELECT
    SECUCODE AS 股票代码,
    stock_name AS 证券名称,
    ROUND(revenue_ttm / 100000000, 2) AS 最新TTM营业收入,
    ROUND(fee_rate_ttm, 2) AS 最新TTM三费率,
    ROUND(fee_rate_drop, 2) AS 较两年前同期三费率降幅,
    ROUND(net_margin_change, 2) AS 同期TTM净利率变化
FROM screened
ORDER BY fee_rate_drop DESC, SECUCODE ASC
LIMIT 10;
