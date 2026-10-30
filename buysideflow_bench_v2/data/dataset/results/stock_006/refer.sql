WITH params AS (
    SELECT CAST('2025-09-05' AS DATE) AS as_of_date
),
listed_asof AS (
    SELECT INNERCODE, CHANGETYPE
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY ls.CHANGEDATE DESC,
                         COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
                         ls.JSID DESC,
                         ls.ID DESC
            ) AS rn
        FROM lc_liststatus ls
        JOIN params p
          ON ls.CHANGEDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
industry_asof AS (
    SELECT COMPANYCODE, SECONDINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.SECONDINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC,
                         e.INSERTTIME DESC,
                         e.JSID DESC,
                         e.ID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN params p
          ON DATE(e.INFOPUBLDATE) <= p.as_of_date
         AND (e.CANCELDATE IS NULL OR DATE(e.CANCELDATE) > p.as_of_date)
        WHERE e.STANDARD = 38
          AND e.FIRSTINDUSTRYNAME IN ('食品饮料', '商贸零售', '社会服务', '美容护理')
    ) ranked
    WHERE rn = 1
),
industry_base AS (
    SELECT
        s.COMPANYCODE,
        s.SECUCODE,
        s.SECUABBR,
        i.SECONDINDUSTRYNAME
    FROM secumain s
    LEFT JOIN listed_asof la
      ON la.INNERCODE = s.INNERCODE
    JOIN industry_asof i
      ON i.COMPANYCODE = s.COMPANYCODE
    JOIN params p
      ON s.LISTEDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR IN (1, 2, 6)
      AND la.CHANGETYPE IN (1, 3)
),
rev_raw AS (
    SELECT
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS ENDDATE,
        m.INFOPUBLDATE,
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
                m.INSERTTIME DESC,
                m.ID DESC
        ) AS rn
    FROM lc_maindatanew m
    JOIN industry_base b
      ON b.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE DATE(m.ENDDATE) >= DATE_SUB(p.as_of_date, INTERVAL 3 YEAR)
      AND m.MARK IN (1, 8, 7, 6, 2)
      AND MONTH(ENDDATE) IN (3, 6, 9, 12)
      AND OPERATINGREENUE IS NOT NULL
),
rev_q AS (
    SELECT COMPANYCODE, ENDDATE,
           CASE
               WHEN MONTH(ENDDATE) = 3 THEN OPERATINGREENUE
               WHEN QUARTER(ENDDATE) - QUARTER(
                        LAG(ENDDATE) OVER (PARTITION BY COMPANYCODE, YEAR(ENDDATE) ORDER BY ENDDATE)) = 1
               THEN OPERATINGREENUE - LAG(OPERATINGREENUE) OVER (
                        PARTITION BY COMPANYCODE, YEAR(ENDDATE)
                        ORDER BY ENDDATE
                    )
           END AS revenue_q,
           ROW_NUMBER() OVER (PARTITION BY COMPANYCODE ORDER BY ENDDATE DESC) AS rn_desc
    FROM rev_raw
    WHERE rn = 1
),
rev_recent AS (
    SELECT COMPANYCODE, ENDDATE, revenue_q, rn_desc
    FROM rev_q
    WHERE rn_desc <= 8
),
rev_stats AS (
    SELECT
        COMPANYCODE,
        MAX(CASE WHEN rn_desc = 1 THEN ENDDATE END) AS latest_report_end,
        SUM(CASE WHEN rn_desc <= 4 THEN revenue_q END) AS revenue_last4q,
        MAX(CASE WHEN rn_desc = 1 AND YEAR(ENDDATE) = YEAR(prev_yoy_date) + 1
                       AND QUARTER(ENDDATE) = QUARTER(prev_yoy_date)
                 THEN (revenue_q / NULLIF(prev_yoy_q, 0) - 1) * 100 END) AS latest_rev_yoy
    FROM (
        SELECT
            COMPANYCODE,
            ENDDATE,
            revenue_q,
            rn_desc,
            LAG(revenue_q, 4) OVER (PARTITION BY COMPANYCODE ORDER BY ENDDATE) AS prev_yoy_q,
            LAG(ENDDATE, 4) OVER (PARTITION BY COMPANYCODE ORDER BY ENDDATE) AS prev_yoy_date
        FROM rev_recent
    ) t
    GROUP BY COMPANYCODE
    HAVING COUNT(CASE WHEN rn_desc <= 4 THEN revenue_q END) = 4
       AND PERIOD_DIFF(EXTRACT(YEAR_MONTH FROM MAX(ENDDATE)),
                       EXTRACT(YEAR_MONTH FROM MIN(CASE WHEN rn_desc <= 4 THEN ENDDATE END))) = 9
),
bs_raw AS (
    SELECT
        b.COMPANYCODE,
        DATE(b.ENDDATE) AS ENDDATE,
        b.INFOPUBLDATE,
        COALESCE(b.CONTRACTLIABILITY, 0) + COALESCE(b.ADVANCERECEIPTS, 0) AS order_liab,
        ROW_NUMBER() OVER (
            PARTITION BY b.COMPANYCODE, DATE(b.ENDDATE)
            ORDER BY b.INFOPUBLDATE DESC, b.INSERTTIME DESC, b.ID DESC
        ) AS rn
    FROM lc_balancesheetall b
    JOIN industry_base ib
      ON ib.COMPANYCODE = b.COMPANYCODE
    JOIN params p
      ON b.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE b.IFMERGED = 1
      AND b.IFCOMPLETE = 1
      AND DATE(b.ENDDATE) >= DATE_SUB(p.as_of_date, INTERVAL 3 YEAR)
      AND MONTH(b.ENDDATE) IN (3, 6, 9, 12)
      AND (b.CONTRACTLIABILITY IS NOT NULL OR b.ADVANCERECEIPTS IS NOT NULL)
),
bs_q AS (
    SELECT COMPANYCODE, ENDDATE, order_liab
    FROM bs_raw
    WHERE rn = 1
),
bs_ranked AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        order_liab,
        ROW_NUMBER() OVER (PARTITION BY COMPANYCODE ORDER BY ENDDATE DESC) AS rn_desc
    FROM bs_q
),
bs_last7 AS (
    SELECT COMPANYCODE, ENDDATE, order_liab, rn_desc
    FROM bs_ranked
    WHERE rn_desc <= 7
),
bs_trend AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        order_liab,
        rn_desc,
        LAG(order_liab) OVER (PARTITION BY COMPANYCODE ORDER BY ENDDATE) AS prev_order_liab
    FROM bs_last7
),
bs_stats AS (
    SELECT
        COMPANYCODE,
        MAX(CASE WHEN rn_desc = 1 THEN ENDDATE END) AS latest_report_end,
        MAX(CASE WHEN rn_desc = 1 THEN order_liab END) AS latest_order_liab,
        SUM(CASE WHEN prev_order_liab IS NOT NULL AND order_liab > prev_order_liab THEN 1 ELSE 0 END) AS up_cnt_6p
    FROM bs_trend
    GROUP BY COMPANYCODE
    HAVING COUNT(*) = 7
       AND PERIOD_DIFF(EXTRACT(YEAR_MONTH FROM MAX(ENDDATE)), EXTRACT(YEAR_MONTH FROM MIN(ENDDATE))) = 18
),
se_raw AS (
    SELECT
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS ENDDATE,
        m.INFOPUBLDATE,
        m.OPERATINGEXPENSERATE,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC, m.INSERTTIME DESC, m.ID DESC
        ) AS rn
    FROM lc_mainindexnew m
    JOIN industry_base ib
      ON ib.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE DATE(m.ENDDATE) >= DATE_SUB(p.as_of_date, INTERVAL 2 YEAR)
      AND m.OPERATINGEXPENSERATE IS NOT NULL
),
se_q AS (
    SELECT COMPANYCODE, ENDDATE, OPERATINGEXPENSERATE
    FROM se_raw
    WHERE rn = 1
),
se_latest AS (
    SELECT COMPANYCODE, ENDDATE, OPERATINGEXPENSERATE
    FROM (
        SELECT
            COMPANYCODE,
            ENDDATE,
            OPERATINGEXPENSERATE,
            ROW_NUMBER() OVER (PARTITION BY COMPANYCODE ORDER BY ENDDATE DESC) AS rn_desc
        FROM se_q
    ) t
    WHERE rn_desc = 1
),
se_cmp AS (
    SELECT
        c.COMPANYCODE,
        c.ENDDATE AS latest_report_end,
        c.OPERATINGEXPENSERATE AS latest_sales_exp_rate,
        p.OPERATINGEXPENSERATE AS prev_year_sales_exp_rate
    FROM se_latest c
    LEFT JOIN se_q p
      ON p.COMPANYCODE = c.COMPANYCODE
     AND YEAR(p.ENDDATE) = YEAR(c.ENDDATE) - 1
     AND QUARTER(p.ENDDATE) = QUARTER(c.ENDDATE)
)
SELECT b.SECUCODE AS 股票代码,
       b.SECUABBR AS 简称,
       b.SECONDINDUSTRYNAME AS 申万二级行业,
       ROUND(bs.latest_order_liab / NULLIF(r.revenue_last4q, 0) * 100, 4) AS 预收_合同负债占收入比例,
       ROUND(r.latest_rev_yoy, 4) AS 单季度营收同比增速,
       ROUND(s.latest_sales_exp_rate, 4) AS 销售费用率
FROM industry_base b
JOIN rev_stats r ON r.COMPANYCODE = b.COMPANYCODE
JOIN bs_stats bs
  ON bs.COMPANYCODE = b.COMPANYCODE
 AND bs.latest_report_end = r.latest_report_end
JOIN se_cmp s
  ON s.COMPANYCODE = b.COMPANYCODE
 AND s.latest_report_end = r.latest_report_end
WHERE bs.latest_order_liab / NULLIF(r.revenue_last4q, 0) * 100 > 20
  AND bs.up_cnt_6p >= 2
  AND r.latest_rev_yoy > 15
  AND s.prev_year_sales_exp_rate IS NOT NULL
  AND s.latest_sales_exp_rate < s.prev_year_sales_exp_rate
ORDER BY 预收_合同负债占收入比例 DESC, 单季度营收同比增速 DESC;
