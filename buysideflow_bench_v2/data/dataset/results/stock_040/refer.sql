WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
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
          ON ls.CHANGEDATE <= p.as_of_date
         AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) ranked
    WHERE rn = 1
      AND CHANGETYPE IN (1, 3)
),
stock_base AS (
    SELECT INNERCODE, COMPANYCODE, SECUCODE, stock_name
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            LPAD(s.SECUCODE, 6, '0') AS SECUCODE,
            COALESCE(NULLIF(s.SECUABBR, ''), NULLIF(s.CHINAMEABBR, ''), s.CHINAME, s.SECUCODE) AS stock_name,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.SECUCODE, s.INNERCODE
            ) AS rn
        FROM secumain s
        JOIN listed_asof ls ON ls.INNERCODE = s.INNERCODE
        JOIN params p ON s.LISTEDDATE <= p.as_of_date
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2, 6)
          AND s.COMPANYCODE IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              WHERE ra.INNERCODE = s.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE)
                    < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
                AND DATE(ra.IMPLEMENTDATE) <= p.as_of_date
                AND (
                    ra.REMOVEDATE IS NULL
                    OR DATE(ra.REMOVEDATE) > p.as_of_date
                    OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE)
                       >= DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
                )
          )
    ) ranked
    WHERE rn = 1
),
industry_versions AS (
    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYNAME,
        ROW_NUMBER() OVER (
            PARTITION BY e.COMPANYCODE
            ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC, e.ID DESC
        ) AS rn
    FROM lc_exgindustry e
    JOIN stock_base sb ON sb.COMPANYCODE = e.COMPANYCODE
    JOIN params p ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE e.STANDARD = 38
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
),
stock_pool AS (
    SELECT sb.*
    FROM stock_base sb
    JOIN industry_versions i
      ON i.COMPANYCODE = sb.COMPANYCODE
     AND i.rn = 1
     AND i.FIRSTINDUSTRYNAME = '电力设备'
),
main_versions AS (
    SELECT
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS end_date,
        m.OPERATINGREENUE,
        m.NETOPERATECASHFLOW,
        m.NETPROFITCUT,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
            ORDER BY CASE m.MARK
                         WHEN 1 THEN 0
                         WHEN 8 THEN 1
                         WHEN 7 THEN 2
                         WHEN 6 THEN 3
                         WHEN 2 THEN 4
                         ELSE 9
                     END,
                     m.INFOPUBLDATE DESC,
                     m.JSID DESC
        ) AS version_rank
    FROM lc_maindatanew m
    JOIN stock_pool sp ON sp.COMPANYCODE = m.COMPANYCODE
    JOIN params p ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE m.ENDDATE >= DATE('2022-01-01')
      AND m.ENDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND MONTH(m.ENDDATE) IN (3, 6, 9, 12)
      AND m.MARK IN (1, 8, 7, 6, 2)
),
main_cumulative AS (
    SELECT COMPANYCODE, end_date, OPERATINGREENUE, NETOPERATECASHFLOW, NETPROFITCUT
    FROM main_versions
    WHERE version_rank = 1
),
main_with_lag AS (
    SELECT
        m.*,
        LAG(end_date) OVER (PARTITION BY COMPANYCODE, YEAR(end_date) ORDER BY end_date) AS prev_end_date,
        LAG(OPERATINGREENUE) OVER (PARTITION BY COMPANYCODE, YEAR(end_date) ORDER BY end_date) AS prev_revenue,
        LAG(NETOPERATECASHFLOW) OVER (PARTITION BY COMPANYCODE, YEAR(end_date) ORDER BY end_date) AS prev_ocf,
        LAG(NETPROFITCUT) OVER (PARTITION BY COMPANYCODE, YEAR(end_date) ORDER BY end_date) AS prev_cut_np
    FROM main_cumulative m
),
single_quarters AS (
    SELECT
        COMPANYCODE,
        end_date,
        CASE
            WHEN MONTH(end_date) = 3 THEN OPERATINGREENUE
            WHEN PERIOD_DIFF(EXTRACT(YEAR_MONTH FROM end_date), EXTRACT(YEAR_MONTH FROM prev_end_date)) = 3
                THEN OPERATINGREENUE - prev_revenue
        END AS single_q_revenue,
        CASE
            WHEN MONTH(end_date) = 3 THEN NETOPERATECASHFLOW
            WHEN PERIOD_DIFF(EXTRACT(YEAR_MONTH FROM end_date), EXTRACT(YEAR_MONTH FROM prev_end_date)) = 3
                THEN NETOPERATECASHFLOW - prev_ocf
        END AS single_q_ocf,
        CASE
            WHEN MONTH(end_date) = 3 THEN NETPROFITCUT
            WHEN PERIOD_DIFF(EXTRACT(YEAR_MONTH FROM end_date), EXTRACT(YEAR_MONTH FROM prev_end_date)) = 3
                THEN NETPROFITCUT - prev_cut_np
        END AS single_q_cut_np
    FROM main_with_lag
),
ranked_quarters AS (
    SELECT
        q.*,
        ROW_NUMBER() OVER (PARTITION BY q.COMPANYCODE ORDER BY q.end_date DESC) AS recency_rank
    FROM single_quarters q
),
latest_report AS (
    SELECT COMPANYCODE, end_date AS latest_report_date
    FROM ranked_quarters
    WHERE recency_rank = 1
),
performance AS (
    SELECT
        cur.COMPANYCODE,
        MAX(cur.end_date) AS latest_report_date,
        MIN(cur.end_date) AS earliest_report_date,
        SUM(cur.single_q_revenue) AS revenue_ttm,
        SUM(prev1.single_q_revenue) AS revenue_ttm_prev,
        SUM(cur.single_q_cut_np) AS cut_np_ttm,
        SUM(prev1.single_q_cut_np) AS cut_np_ttm_prev,
        SUM(cur.single_q_ocf) AS ocf_ttm,
        SUM(prev2.single_q_revenue) AS revenue_ttm_2y_ago,
        COUNT(*) AS quarter_count
    FROM ranked_quarters cur
    JOIN single_quarters prev1
      ON prev1.COMPANYCODE = cur.COMPANYCODE
     AND prev1.end_date = DATE_SUB(cur.end_date, INTERVAL 1 YEAR)
    JOIN single_quarters prev2
      ON prev2.COMPANYCODE = cur.COMPANYCODE
     AND prev2.end_date = DATE_SUB(cur.end_date, INTERVAL 2 YEAR)
    WHERE cur.recency_rank <= 4
      AND cur.single_q_revenue IS NOT NULL
      AND cur.single_q_cut_np IS NOT NULL
      AND cur.single_q_ocf IS NOT NULL
      AND prev1.single_q_revenue IS NOT NULL
      AND prev1.single_q_cut_np IS NOT NULL
      AND prev2.single_q_revenue IS NOT NULL
    GROUP BY cur.COMPANYCODE
),
balance_versions AS (
    SELECT
        b.COMPANYCODE,
        DATE(b.ENDDATE) AS end_date,
        b.TOTALFIXEDASSET,
        b.TCONSTRUINPROCESS,
        ROW_NUMBER() OVER (
            PARTITION BY b.COMPANYCODE, DATE(b.ENDDATE)
            ORDER BY b.INFOPUBLDATE DESC, b.JSID DESC
        ) AS version_rank
    FROM lc_balancesheetall b
    JOIN stock_pool sp ON sp.COMPANYCODE = b.COMPANYCODE
    JOIN params p ON b.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE b.ENDDATE >= DATE('2022-01-01')
      AND b.ENDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND b.IFMERGED = 1
),
balance_clean AS (
    SELECT
        COMPANYCODE,
        end_date,
        TOTALFIXEDASSET + TCONSTRUINPROCESS AS fixed_and_cip
    FROM balance_versions
    WHERE version_rank = 1
      AND TOTALFIXEDASSET IS NOT NULL
      AND TCONSTRUINPROCESS IS NOT NULL
),
balance_pairs AS (
    SELECT
        lr.COMPANYCODE,
        cur.fixed_and_cip AS current_fixed_and_cip,
        old.fixed_and_cip AS fixed_and_cip_2y_ago
    FROM latest_report lr
    JOIN balance_clean cur
      ON cur.COMPANYCODE = lr.COMPANYCODE
     AND cur.end_date = lr.latest_report_date
    JOIN balance_clean old
      ON old.COMPANYCODE = lr.COMPANYCODE
     AND old.end_date = DATE_SUB(lr.latest_report_date, INTERVAL 2 YEAR)
    WHERE old.fixed_and_cip > 0
),
debt_versions AS (
    SELECT
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS end_date,
        m.DEBTASSETSRATIO,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC,
                     m.JSID DESC
        ) AS version_rank
    FROM lc_mainindexnew m
    JOIN stock_pool sp ON sp.COMPANYCODE = m.COMPANYCODE
    JOIN params p ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE m.ENDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
),
debt_clean AS (
    SELECT COMPANYCODE, end_date, DEBTASSETSRATIO
    FROM debt_versions
    WHERE version_rank = 1
),
debt_latest_ranked AS (
    SELECT
        d.*,
        ROW_NUMBER() OVER (PARTITION BY d.COMPANYCODE ORDER BY d.end_date DESC) AS period_rank
    FROM debt_clean d
),
debt_sample AS (
    SELECT COMPANYCODE, DEBTASSETSRATIO
    FROM debt_latest_ranked
    WHERE period_rank = 1
      AND DEBTASSETSRATIO IS NOT NULL
),
debt_ordered AS (
    SELECT
        DEBTASSETSRATIO,
        ROW_NUMBER() OVER (ORDER BY DEBTASSETSRATIO) AS value_rank,
        COUNT(*) OVER () AS sample_count
    FROM debt_sample
),
p75_positions AS (
    SELECT
        MAX(sample_count) AS sample_count,
        FLOOR((MAX(sample_count) - 1) * 0.75) + 1 AS lower_rank,
        CEIL((MAX(sample_count) - 1) * 0.75) + 1 AS upper_rank,
        (MAX(sample_count) - 1) * 0.75 - FLOOR((MAX(sample_count) - 1) * 0.75) AS weight
    FROM debt_ordered
),
debt_p75 AS (
    SELECT
        lo.DEBTASSETSRATIO
        + p.weight * (hi.DEBTASSETSRATIO - lo.DEBTASSETSRATIO) AS p75
    FROM p75_positions p
    JOIN debt_ordered lo ON lo.value_rank = p.lower_rank
    JOIN debt_ordered hi ON hi.value_rank = p.upper_rank
),
analysis_base AS (
    SELECT
        sp.SECUCODE,
        sp.stock_name,
        (perf.revenue_ttm - perf.revenue_ttm_prev) / perf.revenue_ttm_prev * 100 AS revenue_ttm_yoy,
        (bp.current_fixed_and_cip - bp.fixed_and_cip_2y_ago) / bp.fixed_and_cip_2y_ago * 100 AS fixed_and_cip_growth_2y,
        (perf.revenue_ttm - perf.revenue_ttm_2y_ago) / perf.revenue_ttm_2y_ago * 100 AS revenue_ttm_growth_2y,
        debt.DEBTASSETSRATIO,
        p75.p75,
        perf.cut_np_ttm,
        perf.cut_np_ttm_prev,
        perf.ocf_ttm,
        perf.quarter_count,
        perf.latest_report_date,
        perf.earliest_report_date
    FROM stock_pool sp
    JOIN performance perf ON perf.COMPANYCODE = sp.COMPANYCODE
    JOIN balance_pairs bp ON bp.COMPANYCODE = sp.COMPANYCODE
    JOIN debt_clean debt
      ON debt.COMPANYCODE = perf.COMPANYCODE
     AND debt.end_date = perf.latest_report_date
    CROSS JOIN debt_p75 p75
    WHERE perf.quarter_count = 4
      AND PERIOD_DIFF(EXTRACT(YEAR_MONTH FROM perf.latest_report_date),
                      EXTRACT(YEAR_MONTH FROM perf.earliest_report_date)) = 9
      AND perf.revenue_ttm_prev > 0
      AND perf.cut_np_ttm_prev > 0
      AND perf.revenue_ttm_2y_ago > 0
      AND perf.cut_np_ttm > perf.cut_np_ttm_prev
      AND perf.ocf_ttm > 0
      AND debt.DEBTASSETSRATIO IS NOT NULL
)
SELECT
    SECUCODE AS `股票代码`,
    stock_name AS `股票简称`,
    ROUND(revenue_ttm_yoy, 2) AS `营业收入TTM同比增速`,
    ROUND(fixed_and_cip_growth_2y, 2) AS `固定资产与在建工程合计两年增速`,
    ROUND(revenue_ttm_growth_2y, 2) AS `营业收入TTM两年增速`,
    ROUND(DEBTASSETSRATIO, 2) AS `资产负债率`
FROM analysis_base
WHERE revenue_ttm_yoy > 20
  AND fixed_and_cip_growth_2y < revenue_ttm_growth_2y
  AND DEBTASSETSRATIO < p75
ORDER BY (revenue_ttm_growth_2y - fixed_and_cip_growth_2y) DESC,
         SECUCODE ASC
LIMIT 20;
