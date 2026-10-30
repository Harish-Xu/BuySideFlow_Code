WITH
params AS (
    SELECT TIMESTAMP('2025-12-31 23:59:59') AS as_of_dt
),
actual_date AS (
    SELECT MAX(DATE(t.TRADINGDATE)) AS trade_date
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_dt
    WHERE t.SECUMARKET = 83
      AND t.IFTRADINGDAY = 1
),
calendar_60d AS (
    SELECT trade_date
    FROM (
        SELECT DATE(t.TRADINGDATE) AS trade_date
        FROM qt_tradingdaynew t
        JOIN actual_date a
          ON t.TRADINGDATE <= a.trade_date
        WHERE t.SECUMARKET = 83
          AND t.IFTRADINGDAY = 1
        ORDER BY t.TRADINGDATE DESC
        LIMIT 60
    ) d
),
stock_base AS (
    SELECT INNERCODE, COMPANYCODE, SECUCODE, LISTEDSECTOR, stock_name
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUCODE,
            s.LISTEDSECTOR,
            COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name,
            ROW_NUMBER() OVER (PARTITION BY s.COMPANYCODE ORDER BY s.INNERCODE) AS rn
        FROM secumain s
        JOIN actual_date a
          ON s.LISTEDDATE <= DATE_SUB(a.trade_date, INTERVAL 2 YEAR)
        JOIN params p
          ON 1 = 1
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              WHERE ra.INNERCODE = s.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND DATE(ra.IMPLEMENTDATE) <= DATE(p.as_of_dt)
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= p.as_of_dt
                AND (
                    ra.REMOVEDATE IS NULL
                    OR DATE(ra.REMOVEDATE) > DATE(p.as_of_dt)
                    OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) > p.as_of_dt
                )
          )
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
                ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR <> 7
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_dt
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_dt)
    ) t
    WHERE rn = 1
    UNION ALL
    SELECT COMPANYCODE, SECONDINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.SECONDINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.EFFECTIVEDATE DESC, e.INFOPUBLDATE DESC, e.JSID DESC
            ) AS rn
        FROM lc_stibexgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR = 7
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_dt
        WHERE e.STANDARD = 38
          AND e.EFFECTIVEDATE <= p.as_of_dt
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_dt)
    ) t
    WHERE rn = 1
),
target_stocks AS (
    SELECT sb.*, ia.SECONDINDUSTRYNAME
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    WHERE ia.SECONDINDUSTRYNAME IN ('化学制药', '生物制品')
),
liquidity AS (
    SELECT
        ts.INNERCODE,
        AVG(CASE WHEN ts.LISTEDSECTOR = 7 THEN sq.TURNOVERVALUE ELSE q.TURNOVERVALUE END) AS avg_turnover_60d,
        COUNT(c.trade_date) AS trading_days,
        COUNT(CASE WHEN ts.LISTEDSECTOR = 7 THEN sq.TURNOVERVALUE ELSE q.TURNOVERVALUE END) AS valid_turnover_days
    FROM target_stocks ts
    CROSS JOIN calendar_60d c
    LEFT JOIN qt_dailyquote q
      ON q.INNERCODE = ts.INNERCODE
     AND ts.LISTEDSECTOR <> 7
     AND q.TRADINGDAY = c.trade_date
     AND q.TURNOVERVALUE >= 0
    LEFT JOIN lc_stibdailyquote sq
      ON sq.INNERCODE = ts.INNERCODE
     AND ts.LISTEDSECTOR = 7
     AND sq.TRADINGDAY = c.trade_date
     AND sq.TURNOVERVALUE >= 0
    GROUP BY ts.INNERCODE
),
qfin_ver AS (
    SELECT
        q.COMPANYCODE,
        DATE(q.ENDDATE) AS end_date,
        q.OPERATINGREVENUEYOY,
        q.NPPARENTCOMPANYCUTYOY,
        ROW_NUMBER() OVER (
            PARTITION BY q.COMPANYCODE, DATE(q.ENDDATE)
            ORDER BY q.INFOPUBLDATE DESC,
                     COALESCE(q.INSERTTIME, q.INFOPUBLDATE) DESC,
                     q.JSID DESC
        ) AS rn_ver
    FROM lc_qfinancialindexnew q
    JOIN target_stocks ts
      ON ts.COMPANYCODE = q.COMPANYCODE
        JOIN params p
          ON q.INFOPUBLDATE <= p.as_of_dt
    WHERE q.MARK = 2
      AND q.ACCOUNTINGSTANDARDS = 1
      AND q.ENDDATE <= p.as_of_dt
      AND MONTH(q.ENDDATE) IN (3, 6, 9, 12)
),
qfin_series AS (
    SELECT
        COMPANYCODE,
        end_date,
        OPERATINGREVENUEYOY,
        NPPARENTCOMPANYCUTYOY,
        LAG(end_date) OVER (PARTITION BY COMPANYCODE ORDER BY end_date) AS prev_end_date,
        LAG(NPPARENTCOMPANYCUTYOY) OVER (PARTITION BY COMPANYCODE ORDER BY end_date) AS prev_cut_np_yoy,
        ROW_NUMBER() OVER (PARTITION BY COMPANYCODE ORDER BY end_date DESC) AS rn_desc
    FROM qfin_ver
    WHERE rn_ver = 1
),
latest_qfin AS (
    SELECT
        COMPANYCODE,
        end_date,
        OPERATINGREVENUEYOY AS latest_revenue_yoy,
        NPPARENTCOMPANYCUTYOY - prev_cut_np_yoy AS cut_np_yoy_improvement
    FROM qfin_series
    WHERE rn_desc = 1
      AND PERIOD_DIFF(EXTRACT(YEAR_MONTH FROM end_date),
                      EXTRACT(YEAR_MONTH FROM prev_end_date)) = 3
),
main_ver AS (
    SELECT
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS end_date,
        m.OPERATINGREENUE,
        m.NETOPERATECASHFLOW,
        m.CASHEQUIALENTS,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
            ORDER BY CASE m.MARK
                         WHEN 1 THEN 0 WHEN 8 THEN 1 WHEN 7 THEN 2
                         WHEN 6 THEN 3 WHEN 2 THEN 4 ELSE 9
                     END,
                     m.INFOPUBLDATE DESC,
                     COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                     m.JSID DESC
        ) AS rn_ver
    FROM lc_maindatanew m
    JOIN target_stocks ts
      ON ts.COMPANYCODE = m.COMPANYCODE
        JOIN params p
          ON m.INFOPUBLDATE <= p.as_of_dt
    WHERE m.ENDDATE <= p.as_of_dt
      AND m.ENDDATE >= DATE('2023-01-01')
      AND MONTH(m.ENDDATE) IN (3, 6, 9, 12)
      AND m.MARK IN (1, 8, 7, 6, 2)
      AND COALESCE(m.ACCOUNTINGSTANDARDS, 1) = 1
),
rd_ver AS (
    SELECT
        f.COMPANYCODE,
        DATE(f.ENDDATE) AS end_date,
        f.RANDDEXPENSERATE,
        f.TOTALOPERATEREVENUEPS,
        f.TOTALSHARES,
        ROW_NUMBER() OVER (
            PARTITION BY f.COMPANYCODE, DATE(f.ENDDATE)
            ORDER BY CASE f.MARK
                         WHEN 1 THEN 0 WHEN 8 THEN 1 WHEN 7 THEN 2
                         WHEN 6 THEN 3 WHEN 2 THEN 4 ELSE 9
                     END,
                     f.INFOPUBLDATE DESC,
                     COALESCE(f.INSERTTIME, f.INFOPUBLDATE) DESC,
                     f.JSID DESC
        ) AS rn_ver
    FROM fin_derivative f
    JOIN target_stocks ts
      ON ts.COMPANYCODE = f.COMPANYCODE
        JOIN params p
          ON f.INFOPUBLDATE <= p.as_of_dt
    WHERE f.ENDDATE <= p.as_of_dt
      AND f.ENDDATE >= DATE('2023-01-01')
      AND MONTH(f.ENDDATE) IN (3, 6, 9, 12)
      AND f.MARK IN (1, 8, 7, 6, 2)
),
cumulative_fin AS (
    SELECT
        m.COMPANYCODE,
        m.end_date,
        m.OPERATINGREENUE,
        m.NETOPERATECASHFLOW,
        m.CASHEQUIALENTS,
        r.TOTALOPERATEREVENUEPS * r.TOTALSHARES
            * r.RANDDEXPENSERATE / 100 AS cumulative_rd_expense
    FROM main_ver m
    JOIN rd_ver r
      ON r.COMPANYCODE = m.COMPANYCODE
     AND r.end_date = m.end_date
     AND r.rn_ver = 1
    WHERE m.rn_ver = 1
      AND m.OPERATINGREENUE IS NOT NULL
      AND m.NETOPERATECASHFLOW IS NOT NULL
      AND r.RANDDEXPENSERATE IS NOT NULL
      AND r.TOTALOPERATEREVENUEPS IS NOT NULL
      AND r.TOTALSHARES > 0
),
quarter_base AS (
    SELECT
        cf.*,
        LAG(end_date) OVER (PARTITION BY COMPANYCODE, YEAR(end_date) ORDER BY end_date) AS prev_end_date,
        LAG(OPERATINGREENUE) OVER (PARTITION BY COMPANYCODE, YEAR(end_date) ORDER BY end_date) AS prev_revenue,
        LAG(NETOPERATECASHFLOW) OVER (PARTITION BY COMPANYCODE, YEAR(end_date) ORDER BY end_date) AS prev_ocf,
        LAG(cumulative_rd_expense) OVER (PARTITION BY COMPANYCODE, YEAR(end_date) ORDER BY end_date) AS prev_rd_expense
    FROM cumulative_fin cf
),
quarter_fin AS (
    SELECT
        COMPANYCODE,
        end_date,
        CASHEQUIALENTS,
        CASE
            WHEN MONTH(end_date) = 3 THEN OPERATINGREENUE
            WHEN PERIOD_DIFF(EXTRACT(YEAR_MONTH FROM end_date),
                             EXTRACT(YEAR_MONTH FROM prev_end_date)) = 3 THEN OPERATINGREENUE - prev_revenue
        END AS single_q_revenue,
        CASE
            WHEN MONTH(end_date) = 3 THEN NETOPERATECASHFLOW
            WHEN PERIOD_DIFF(EXTRACT(YEAR_MONTH FROM end_date),
                             EXTRACT(YEAR_MONTH FROM prev_end_date)) = 3 THEN NETOPERATECASHFLOW - prev_ocf
        END AS single_q_ocf,
        CASE
            WHEN MONTH(end_date) = 3 THEN cumulative_rd_expense
            WHEN PERIOD_DIFF(EXTRACT(YEAR_MONTH FROM end_date),
                             EXTRACT(YEAR_MONTH FROM prev_end_date)) = 3 THEN cumulative_rd_expense - prev_rd_expense
        END AS single_q_rd_expense,
        ROW_NUMBER() OVER (PARTITION BY COMPANYCODE ORDER BY end_date DESC) AS rn_desc
    FROM quarter_base
),
last4_ops AS (
    SELECT
        COMPANYCODE,
        COUNT(*) AS quarter_count,
        MIN(end_date) AS first_end_date,
        MAX(end_date) AS latest_end_date,
        SUM(single_q_revenue) AS revenue_ttm,
        SUM(single_q_ocf) AS ocf_sum_4q,
        SUM(single_q_rd_expense) AS rd_expense_sum_4q,
        MAX(CASE WHEN rn_desc = 1 THEN CASHEQUIALENTS END) AS cash_balance
    FROM quarter_fin
    WHERE rn_desc <= 4
      AND single_q_revenue IS NOT NULL
      AND single_q_ocf IS NOT NULL
      AND single_q_rd_expense IS NOT NULL
    GROUP BY COMPANYCODE
),
analysis_base AS (
    SELECT
        ts.SECUCODE,
        ts.stock_name,
        ts.SECONDINDUSTRYNAME,
        lo.rd_expense_sum_4q / NULLIF(lo.revenue_ttm, 0) * 100 AS rd_expense_rate,
        lo.cash_balance / NULLIF(lo.rd_expense_sum_4q, 0) AS cash_to_rd_expense,
        lo.ocf_sum_4q / NULLIF(lo.cash_balance, 0) AS ocf_to_cash,
        lq.latest_revenue_yoy,
        lq.cut_np_yoy_improvement
    FROM target_stocks ts
    JOIN liquidity li
      ON li.INNERCODE = ts.INNERCODE
    JOIN last4_ops lo
      ON lo.COMPANYCODE = ts.COMPANYCODE
    JOIN latest_qfin lq
      ON lq.COMPANYCODE = ts.COMPANYCODE
     AND lq.end_date = lo.latest_end_date
    WHERE li.trading_days = 60
      AND li.valid_turnover_days = 60
      AND li.avg_turnover_60d >= 50000000
      AND lo.quarter_count = 4
      AND PERIOD_DIFF(EXTRACT(YEAR_MONTH FROM lo.latest_end_date),
                      EXTRACT(YEAR_MONTH FROM lo.first_end_date)) = 9
      AND lo.revenue_ttm > 0
      AND lo.rd_expense_sum_4q > 0
      AND lo.cash_balance > 0
      AND lo.rd_expense_sum_4q / lo.revenue_ttm > 0.15
      AND lo.cash_balance / lo.rd_expense_sum_4q >= 2
      AND lo.ocf_sum_4q / lo.cash_balance >= -0.5
      AND lq.latest_revenue_yoy > 0
      AND lq.cut_np_yoy_improvement > 0
)
SELECT
    SECUCODE AS `股票代码`,
    stock_name AS `股票简称`,
    SECONDINDUSTRYNAME AS `申万二级行业`,
    ROUND(rd_expense_rate, 4) AS `研发费用率(%)`,
    ROUND(cash_to_rd_expense, 4) AS `货币资金/近4季研发费用`,
    ROUND(ocf_to_cash, 4) AS `近4季经营现金流净额/货币资金`,
    ROUND(latest_revenue_yoy, 4) AS `最新单季营业收入同比(%)`,
    ROUND(cut_np_yoy_improvement, 4) AS `扣非归母净利润同比改善幅度(百分点)`
FROM analysis_base
ORDER BY cash_to_rd_expense DESC, SECUCODE ASC;
