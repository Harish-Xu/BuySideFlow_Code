WITH index_map AS (
    SELECT
        CASE s.SECUCODE
            WHEN '931052' THEN '中证国信价值'
            WHEN '980081' THEN '国证价值100'
        END AS index_name,
        s.INNERCODE AS index_innercode
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE IN ('931052', '980081')
),
quarter_dates AS (
    SELECT DATE('2025-09-30') AS end_date UNION ALL
    SELECT DATE('2025-06-30') UNION ALL
    SELECT DATE('2025-03-31') UNION ALL
    SELECT DATE('2024-12-31') UNION ALL
    SELECT DATE('2024-09-30') UNION ALL
    SELECT DATE('2024-06-30')
),
components AS (
    SELECT DISTINCT
        im.index_name,
        s.COMPANYCODE
    FROM index_map im
    JOIN lc_indexcomponent ic
      ON ic.INDEXINNERCODE = im.index_innercode
     AND ic.INDATE <= '2025-10-28'
     AND (ic.OUTDATE IS NULL OR ic.OUTDATE > '2025-10-28')
    JOIN secumain s
      ON s.INNERCODE = ic.SECUINNERCODE
     AND s.SECUCATEGORY = 1
    WHERE s.COMPANYCODE IS NOT NULL
),
company_pool AS (
    SELECT DISTINCT COMPANYCODE FROM components
),
roe_ranked AS (
    SELECT
        c.COMPANYCODE,
        q.end_date,
        mi.ROE,
        ROW_NUMBER() OVER (
            PARTITION BY c.COMPANYCODE, q.end_date
            ORDER BY mi.INFOPUBLDATE DESC, mi.INSERTTIME DESC, mi.ID DESC
        ) AS rn
    FROM company_pool c
    CROSS JOIN quarter_dates q
    JOIN lc_qfinancialindexnew mi
      ON mi.COMPANYCODE = c.COMPANYCODE
     AND mi.ENDDATE = q.end_date
     AND mi.INFOPUBLDATE < DATE('2025-10-29')
),
cumulative_ranked AS (
    SELECT
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS end_date,
        m.OPERATINGREENUE,
        m.NETOPERATECASHFLOW,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY CASE m.MARK
                         WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                         WHEN 6 THEN 4 WHEN 2 THEN 5
                     END,
                     m.INFOPUBLDATE DESC, m.INSERTTIME DESC, m.JSID DESC, m.ID DESC
        ) AS rn
    FROM lc_maindatanew m
    JOIN company_pool c ON c.COMPANYCODE = m.COMPANYCODE
    WHERE m.INFOPUBLDATE < DATE('2025-10-29')
      -- 按通用规则选累计报表版本；MARK 4/5 已是7—9月单季，不能再差分。
      AND m.MARK IN (1, 8, 7, 6, 2)
      AND m.ENDDATE IN (
          '2024-03-31', '2024-06-30', '2024-09-30', '2024-12-31',
          '2025-03-31', '2025-06-30', '2025-09-30'
      )
),
cumulative AS (
    SELECT * FROM cumulative_ranked WHERE rn = 1
),
quarter_amounts AS (
    SELECT
        cur.COMPANYCODE,
        cur.end_date,
        CASE WHEN MONTH(cur.end_date) = 3 THEN cur.OPERATINGREENUE
             ELSE cur.OPERATINGREENUE - prev.OPERATINGREENUE
        END AS quarter_revenue,
        CASE WHEN MONTH(cur.end_date) = 3 THEN cur.NETOPERATECASHFLOW
             ELSE cur.NETOPERATECASHFLOW - prev.NETOPERATECASHFLOW
        END AS quarter_cashflow
    FROM cumulative cur
    JOIN quarter_dates q ON q.end_date = cur.end_date
    LEFT JOIN cumulative prev
      ON prev.COMPANYCODE = cur.COMPANYCODE
     AND prev.end_date = LAST_DAY(DATE_SUB(cur.end_date, INTERVAL 3 MONTH))
     AND MONTH(cur.end_date) <> 3
),
financial_sample AS (
    SELECT
        c.index_name,
        r.COMPANYCODE,
        r.end_date,
        r.ROE,
        CAST(a.quarter_cashflow AS DECIMAL(38, 16)) / a.quarter_revenue * 100
            AS cashflow_ratio
    FROM components c
    JOIN roe_ranked r ON r.COMPANYCODE = c.COMPANYCODE AND r.rn = 1
    JOIN quarter_amounts a ON a.COMPANYCODE = r.COMPANYCODE AND a.end_date = r.end_date
    -- 先选唯一可见版本，再排除所需字段缺失的公司季度，不回退到旧版本。
    WHERE r.ROE IS NOT NULL
      AND a.quarter_cashflow IS NOT NULL
      AND a.quarter_revenue > 0
)
SELECT
    index_name AS `指数名称`,
    COUNT(*) AS `公司季度样本数`,
    ROUND(AVG(ROE), 4) AS `单季ROE均值`,
    ROUND(MIN(ROE), 4) AS `单季ROE最小值`,
    ROUND(AVG(cashflow_ratio), 4) AS `单季经营现金流率均值`,
    ROUND(MIN(cashflow_ratio), 4) AS `单季经营现金流率最小值`
FROM financial_sample
GROUP BY index_name
ORDER BY FIELD(index_name, '中证国信价值', '国证价值100');
