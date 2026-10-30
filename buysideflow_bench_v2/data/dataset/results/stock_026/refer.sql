WITH
params AS (
    SELECT TIMESTAMP('2025-05-31 23:59:59') AS as_of_dt
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
    SELECT
        INNERCODE,
        COMPANYCODE,
        SECUCODE,
        LISTEDSECTOR,
        stock_name
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUCODE,
            s.LISTEDSECTOR,
            COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.INNERCODE
            ) AS rn
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
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= p.as_of_dt
                AND DATE(ra.IMPLEMENTDATE) <= DATE(p.as_of_dt)
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
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC,
                         e.JSID DESC
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
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
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
liquidity AS (
    SELECT
        sb.INNERCODE,
        AVG(COALESCE(
            CASE WHEN sb.LISTEDSECTOR = 7 THEN sq.TURNOVERVALUE ELSE q.TURNOVERVALUE END,
            0
        )) AS avg_turnover_60d,
        COUNT(c.trade_date) AS turnover_days
    FROM stock_base sb
    CROSS JOIN calendar_60d c
    LEFT JOIN qt_dailyquote q
      ON q.INNERCODE = sb.INNERCODE
     AND sb.LISTEDSECTOR <> 7
     AND q.TRADINGDAY = c.trade_date
    LEFT JOIN lc_stibdailyquote sq
      ON sq.INNERCODE = sb.INNERCODE
     AND sb.LISTEDSECTOR = 7
     AND sq.TRADINGDAY = c.trade_date
    GROUP BY sb.INNERCODE
),
qfin_ver AS (
    SELECT
        q.COMPANYCODE,
        DATE(q.ENDDATE) AS end_date,
        q.OPERATINGREVENUEYOY,
        q.GROSSINCOMERATIO,
        ROW_NUMBER() OVER (
            PARTITION BY q.COMPANYCODE, DATE(q.ENDDATE)
            ORDER BY q.INFOPUBLDATE DESC, q.JSID DESC
        ) AS rn_ver
    FROM lc_qfinancialindexnew q
    JOIN stock_base sb
      ON sb.COMPANYCODE = q.COMPANYCODE
    JOIN industry_asof ia
      ON ia.COMPANYCODE = q.COMPANYCODE
     AND ia.FIRSTINDUSTRYNAME IN ('食品饮料', '家用电器', '纺织服饰', '商贸零售')
    JOIN params p
      ON q.INFOPUBLDATE <= p.as_of_dt
     AND COALESCE(q.INSERTTIME, q.INFOPUBLDATE) < DATE_ADD(DATE(p.as_of_dt), INTERVAL 1 DAY)
    WHERE q.ENDDATE <= p.as_of_dt
      AND q.ENDDATE >= '2023-01-01'
      AND MONTH(q.ENDDATE) IN (3, 6, 9, 12)
),
qfin_series AS (
    SELECT
        COMPANYCODE,
        end_date,
        OPERATINGREVENUEYOY,
        GROSSINCOMERATIO,
        ROW_NUMBER() OVER (
            PARTITION BY COMPANYCODE
            ORDER BY end_date DESC
        ) AS rn_desc
    FROM qfin_ver
    WHERE rn_ver = 1
),
latest_qfin AS (
    SELECT
        COMPANYCODE,
        end_date AS latest_report_date,
        OPERATINGREVENUEYOY AS revenue_yoy
    FROM qfin_series
    WHERE rn_desc = 1
),
gross_margin_4q AS (
    SELECT
        COMPANYCODE,
        STDDEV_POP(GROSSINCOMERATIO) AS gross_margin_std_4q,
        COUNT(*) AS gross_margin_q_count,
        MIN(end_date) AS earliest_end_date,
        MAX(end_date) AS latest_end_date
    FROM qfin_series
    WHERE rn_desc <= 4
      AND GROSSINCOMERATIO IS NOT NULL
    GROUP BY COMPANYCODE
),
main_ver AS (
    SELECT
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS end_date,
        m.NETOPERATECASHFLOW,
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
                     m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn_ver
    FROM lc_maindatanew m
    JOIN stock_base sb
      ON sb.COMPANYCODE = m.COMPANYCODE
    JOIN industry_asof ia
      ON ia.COMPANYCODE = m.COMPANYCODE
     AND ia.FIRSTINDUSTRYNAME IN ('食品饮料', '家用电器', '纺织服饰', '商贸零售')
    JOIN params p
      ON m.INFOPUBLDATE <= p.as_of_dt
     AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD(DATE(p.as_of_dt), INTERVAL 1 DAY)
    WHERE m.ENDDATE <= p.as_of_dt
      AND m.ENDDATE >= '2023-01-01'
      AND MONTH(m.ENDDATE) IN (3, 6, 9, 12)
      AND m.MARK IN (1, 8, 7, 6, 2)
),
main_q_base AS (
    SELECT
        COMPANYCODE,
        end_date,
        NETOPERATECASHFLOW,
        LAG(end_date) OVER (
            PARTITION BY COMPANYCODE, YEAR(end_date)
            ORDER BY end_date
        ) AS prev_end_date,
        LAG(NETOPERATECASHFLOW) OVER (
            PARTITION BY COMPANYCODE, YEAR(end_date)
            ORDER BY end_date
        ) AS prev_cumulative_ocf
    FROM main_ver
    WHERE rn_ver = 1
),
main_q AS (
    SELECT
        COMPANYCODE,
        end_date,
        CASE
            WHEN MONTH(end_date) = 3 THEN NETOPERATECASHFLOW
            WHEN PERIOD_DIFF(EXTRACT(YEAR_MONTH FROM end_date),
                             EXTRACT(YEAR_MONTH FROM prev_end_date)) = 3
            THEN NETOPERATECASHFLOW - prev_cumulative_ocf
        END AS single_q_ocf
    FROM main_q_base
),
main_q_ranked AS (
    SELECT
        mq.*,
        ROW_NUMBER() OVER (
            PARTITION BY COMPANYCODE
            ORDER BY end_date DESC
        ) AS rn_desc
    FROM main_q mq
),
ocf_4q AS (
    SELECT
        COMPANYCODE,
        SUM(single_q_ocf) AS ocf_sum_4q,
        COUNT(*) AS ocf_q_count,
        MIN(end_date) AS earliest_end_date,
        MAX(end_date) AS latest_end_date
    FROM main_q_ranked
    WHERE rn_desc <= 4
      AND single_q_ocf IS NOT NULL
    GROUP BY COMPANYCODE
),
balance_ver AS (
    SELECT
        b.COMPANYCODE,
        DATE(b.ENDDATE) AS end_date,
        COALESCE(b.CONTRACTLIABILITY, 0) + COALESCE(b.ADVANCERECEIPTS, 0) AS advance_liability,
        ROW_NUMBER() OVER (
            PARTITION BY b.COMPANYCODE, DATE(b.ENDDATE)
            ORDER BY CASE WHEN b.IFMERGED = 1 THEN 0 ELSE 1 END,
                     b.INFOPUBLDATE DESC,
                     b.JSID DESC
        ) AS rn_ver
    FROM lc_balancesheetall b
    JOIN stock_base sb
      ON sb.COMPANYCODE = b.COMPANYCODE
    JOIN industry_asof ia
      ON ia.COMPANYCODE = b.COMPANYCODE
     AND ia.FIRSTINDUSTRYNAME IN ('食品饮料', '家用电器', '纺织服饰', '商贸零售')
    JOIN params p
      ON b.INFOPUBLDATE <= p.as_of_dt
     AND COALESCE(b.INSERTTIME, b.INFOPUBLDATE) < DATE_ADD(DATE(p.as_of_dt), INTERVAL 1 DAY)
    WHERE b.ENDDATE <= p.as_of_dt
      AND b.ENDDATE >= '2023-01-01'
      AND MONTH(b.ENDDATE) IN (3, 6, 9, 12)
),
balance_latest AS (
    SELECT
        COMPANYCODE,
        end_date,
        advance_liability
    FROM (
        SELECT
            COMPANYCODE,
            end_date,
            advance_liability,
            ROW_NUMBER() OVER (
                PARTITION BY COMPANYCODE
                ORDER BY end_date DESC
            ) AS rn_desc
        FROM balance_ver
        WHERE rn_ver = 1
    ) t
    WHERE rn_desc = 1
),
advance_yoy AS (
    SELECT
        cur.COMPANYCODE,
        cur.end_date,
        cur.advance_liability,
        prev.advance_liability AS advance_liability_prev_year,
        (cur.advance_liability - prev.advance_liability)
            / NULLIF(ABS(prev.advance_liability), 0) * 100 AS advance_liability_yoy
    FROM balance_latest cur
    JOIN balance_ver prev
      ON prev.COMPANYCODE = cur.COMPANYCODE
     AND prev.end_date = DATE_SUB(cur.end_date, INTERVAL 1 YEAR)
     AND prev.rn_ver = 1
    WHERE prev.advance_liability > 0
),
pre_base AS (
    SELECT
        sb.SECUCODE,
        sb.stock_name,
        ia.FIRSTINDUSTRYNAME,
        lq.revenue_yoy,
        ay.advance_liability_yoy,
        ay.advance_liability_yoy - lq.revenue_yoy AS advance_minus_revenue_yoy,
        gm.gross_margin_std_4q,
        ocf.ocf_sum_4q
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    JOIN liquidity li
      ON li.INNERCODE = sb.INNERCODE
    JOIN latest_qfin lq
      ON lq.COMPANYCODE = sb.COMPANYCODE
    JOIN advance_yoy ay
      ON ay.COMPANYCODE = sb.COMPANYCODE
    JOIN gross_margin_4q gm
      ON gm.COMPANYCODE = sb.COMPANYCODE
    JOIN ocf_4q ocf
      ON ocf.COMPANYCODE = sb.COMPANYCODE
    WHERE ia.FIRSTINDUSTRYNAME IN ('食品饮料', '家用电器', '纺织服饰', '商贸零售')
      AND li.turnover_days = 60
      AND li.avg_turnover_60d >= 50000000
      AND lq.revenue_yoy > 15
      AND ay.advance_liability_yoy > lq.revenue_yoy
      AND gm.gross_margin_q_count = 4
      AND gm.latest_end_date = lq.latest_report_date
      AND PERIOD_DIFF(EXTRACT(YEAR_MONTH FROM gm.latest_end_date),
                      EXTRACT(YEAR_MONTH FROM gm.earliest_end_date)) = 9
      AND ocf.ocf_q_count = 4
      AND ocf.latest_end_date = lq.latest_report_date
      AND PERIOD_DIFF(EXTRACT(YEAR_MONTH FROM ocf.latest_end_date),
                      EXTRACT(YEAR_MONTH FROM ocf.earliest_end_date)) = 9
      AND ay.end_date = lq.latest_report_date
      AND ocf.ocf_sum_4q > 0
),
gross_margin_universe AS (
    SELECT
        ia.FIRSTINDUSTRYNAME,
        gm.gross_margin_std_4q
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    JOIN liquidity li
      ON li.INNERCODE = sb.INNERCODE
    JOIN gross_margin_4q gm
      ON gm.COMPANYCODE = sb.COMPANYCODE
    WHERE ia.FIRSTINDUSTRYNAME IN ('食品饮料', '家用电器', '纺织服饰', '商贸零售')
      AND li.turnover_days = 60
      AND li.avg_turnover_60d >= 50000000
      AND gm.gross_margin_q_count = 4
      AND PERIOD_DIFF(EXTRACT(YEAR_MONTH FROM gm.latest_end_date),
                      EXTRACT(YEAR_MONTH FROM gm.earliest_end_date)) = 9
),
industry_gm_median_ranked AS (
    SELECT
        FIRSTINDUSTRYNAME,
        gross_margin_std_4q,
        ROW_NUMBER() OVER (
            PARTITION BY FIRSTINDUSTRYNAME
            ORDER BY gross_margin_std_4q
        ) AS rn_asc,
        COUNT(*) OVER (PARTITION BY FIRSTINDUSTRYNAME) AS cnt
    FROM gross_margin_universe
),
industry_gm_median AS (
    SELECT
        FIRSTINDUSTRYNAME,
        AVG(gross_margin_std_4q) AS industry_gross_margin_std_median
    FROM industry_gm_median_ranked
    WHERE rn_asc IN (FLOOR((cnt + 1) / 2), FLOOR((cnt + 2) / 2))
    GROUP BY FIRSTINDUSTRYNAME
)
SELECT
    pb.SECUCODE AS `股票代码`,
    pb.stock_name AS `股票简称`,
    pb.FIRSTINDUSTRYNAME AS `申万一级行业`,
    ROUND(pb.revenue_yoy, 2) AS `营业收入同比增速(%)`,
    ROUND(pb.advance_liability_yoy, 2) AS `预收类负债同比增速(%)`,
    ROUND(pb.advance_minus_revenue_yoy, 2) AS `预收类负债增速与营收增速差(百分点)`,
    ROUND(pb.gross_margin_std_4q, 4) AS `近4季毛利率标准差`,
    ROUND(pb.ocf_sum_4q / 100000000, 2) AS `近4季经营现金流合计(亿元)`
FROM pre_base pb
JOIN industry_gm_median im
  ON im.FIRSTINDUSTRYNAME = pb.FIRSTINDUSTRYNAME
WHERE pb.gross_margin_std_4q < im.industry_gross_margin_std_median
ORDER BY pb.advance_minus_revenue_yoy DESC, pb.SECUCODE ASC;
