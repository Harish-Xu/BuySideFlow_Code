WITH
params AS (
    SELECT
        DATE('2025-12-31') AS as_of_date,
        DATE_ADD(DATE('2025-12-31'), INTERVAL 1 DAY) AS visible_before
),
trade_asof AS (
    SELECT MAX(t.TRADINGDATE) AS trade_day
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET IN (83, 90)
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
        FROM lc_liststatus ls
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
        sm.INNERCODE,
        sm.COMPANYCODE,
        LPAD(sm.SECUCODE, 6, '0') AS stock_code,
        COALESCE(NULLIF(sm.CHINAMEABBR, ''), NULLIF(sm.SECUABBR, ''), NULLIF(sm.CHINAME, ''), sm.SECUCODE) AS stock_name,
        ROW_NUMBER() OVER (
            PARTITION BY sm.COMPANYCODE
            ORDER BY
                CASE WHEN sm.LISTEDSECTOR = 1 THEN 0 ELSE 1 END,
                sm.SECUCODE,
                sm.INNERCODE
        ) AS rn
    FROM secumain sm
    JOIN trade_asof ta
      ON sm.LISTEDDATE <= ta.trade_day
    LEFT JOIN list_status_asof lsa
      ON lsa.INNERCODE = sm.INNERCODE
    WHERE sm.SECUCATEGORY = 1
      AND sm.SECUMARKET IN (83, 90)
      AND sm.LISTEDSECTOR IN (1, 2, 6)
      AND sm.COMPANYCODE IS NOT NULL
      AND lsa.change_type IN (1, 3)
),
stock_base AS (
    SELECT INNERCODE, COMPANYCODE, stock_code, stock_name
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
suspended AS (
    SELECT DISTINCT sb.INNERCODE
    FROM stock_base sb
    JOIN lc_suspendresumption sr
      ON sr.INNERCODE = sb.INNERCODE
    JOIN trade_asof ta
      ON DATE(sr.SUSPENDDATE) <= ta.trade_day
    JOIN params p
      ON COALESCE(sr.INFOPUBLDATE, sr.INSERTTIME, sr.SUSPENDDATE) < p.visible_before
    WHERE sr.RESUMPTIONDATE IS NULL
       OR DATE(sr.RESUMPTIONDATE) > ta.trade_day
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
industry_raw AS (
    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYNAME AS industry_name,
        ROW_NUMBER() OVER (
            PARTITION BY e.COMPANYCODE
            ORDER BY
                e.INFOPUBLDATE DESC,
                COALESCE(e.CANCELDATE, DATE('9999-12-31')) DESC,
                e.JSID DESC
        ) AS rn
    FROM lc_exgindustry e
    JOIN stock_base sb
      ON sb.COMPANYCODE = e.COMPANYCODE
    JOIN trade_asof ta
      ON e.INFOPUBLDATE < DATE_ADD(ta.trade_day, INTERVAL 1 DAY)
    WHERE e.STANDARD = 38
      AND e.FIRSTINDUSTRYNAME IS NOT NULL
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > (SELECT trade_day FROM trade_asof))
),
industry_asof AS (
    SELECT COMPANYCODE, industry_name
    FROM industry_raw
    WHERE rn = 1
      AND industry_name NOT IN ('银行', '非银金融', '房地产')
),
eligible_stock AS (
    SELECT sb.INNERCODE, sb.COMPANYCODE, sb.stock_code, sb.stock_name
    FROM stock_base sb
    JOIN normal_quote nq
      ON nq.INNERCODE = sb.INNERCODE
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    LEFT JOIN risk_alert_active ra
      ON ra.INNERCODE = sb.INNERCODE
    LEFT JOIN suspended su
      ON su.INNERCODE = sb.INNERCODE
    WHERE ra.INNERCODE IS NULL
      AND su.INNERCODE IS NULL
),
valuation_raw AS (
    SELECT
        es.INNERCODE,
        v.PB,
        v.NEGOTIABLEMV,
        ROW_NUMBER() OVER (
            PARTITION BY es.INNERCODE
            ORDER BY COALESCE(v.INSERTTIME, v.TRADINGDAY) DESC, v.JSID DESC
        ) AS rn
    FROM eligible_stock es
    JOIN lc_dindicesforvaluation v
      ON v.INNERCODE = es.INNERCODE
    JOIN trade_asof ta
      ON v.TRADINGDAY = ta.trade_day
    WHERE v.PB IS NOT NULL
      AND v.PB > 0
      AND v.NEGOTIABLEMV IS NOT NULL
      AND v.NEGOTIABLEMV > 0
),
quarter_fin_raw AS (
    SELECT
        q.COMPANYCODE,
        DATE(q.ENDDATE) AS end_date,
        q.ROE,
        q.EPS,
        q.MARK,
        ROW_NUMBER() OVER (
            PARTITION BY q.COMPANYCODE, DATE(q.ENDDATE)
            ORDER BY
                CASE q.MARK
                    WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                    WHEN 6 THEN 4 WHEN 2 THEN 5 ELSE 99 END,
                q.INFOPUBLDATE DESC,
                COALESCE(q.INSERTTIME, q.INFOPUBLDATE) DESC,
                q.JSID DESC,
                q.ID DESC
        ) AS rn
    FROM lc_qfinancialindexnew q
    JOIN eligible_stock es
      ON es.COMPANYCODE = q.COMPANYCODE
    JOIN params p
      ON q.INFOPUBLDATE < p.visible_before
    JOIN trade_asof ta
      ON q.ENDDATE <= ta.trade_day
    WHERE q.MARK IN (1, 8, 7, 6, 2)
      AND DATE_FORMAT(q.ENDDATE, '%m-%d') IN ('03-31', '06-30', '09-30', '12-31')
),
quarter_fin AS (
    SELECT
        COMPANYCODE,
        end_date,
        YEAR(end_date) * 4 + QUARTER(end_date) AS qtr_no,
        ROE,
        EPS
    FROM quarter_fin_raw
    WHERE rn = 1
),
latest_fin AS (
    SELECT COMPANYCODE, latest_end_date, latest_qtr_no, latest_roe, EPS
    FROM (
        SELECT
            q.COMPANYCODE,
            q.end_date AS latest_end_date,
            q.qtr_no AS latest_qtr_no,
            q.ROE AS latest_roe,
            q.EPS,
            ROW_NUMBER() OVER (
                PARTITION BY q.COMPANYCODE
                ORDER BY q.end_date DESC
            ) AS rn
        FROM quarter_fin q
    ) x
    WHERE rn = 1
      AND latest_roe > 0
      AND EPS > 0
),
pb_universe AS (
    SELECT
        es.COMPANYCODE,
        es.INNERCODE,
        es.stock_code,
        es.stock_name,
        v.PB,
        lf.EPS,
        lf.latest_roe,
        lf.latest_end_date,
        lf.latest_qtr_no,
        v.NEGOTIABLEMV
    FROM eligible_stock es
    JOIN valuation_raw v
      ON v.INNERCODE = es.INNERCODE
     AND v.rn = 1
    JOIN latest_fin lf
      ON lf.COMPANYCODE = es.COMPANYCODE
),
pb_ranked AS (
    SELECT
        pu.*,
        ROW_NUMBER() OVER (ORDER BY pu.PB ASC, pu.stock_code) AS pb_rank,
        COUNT(*) OVER () AS pb_total
    FROM pb_universe pu
),
top_pb AS (
    SELECT *
    FROM pb_ranked
    WHERE pb_rank <= CEIL(pb_total * 0.50)
),
roe_features AS (
    SELECT
        t.COMPANYCODE,
        t.INNERCODE,
        t.stock_code,
        t.stock_name,
        t.pb_rank,
        t.pb_total,
        t.PB,
        t.EPS,
        t.latest_roe,
        t.NEGOTIABLEMV,
        t.latest_roe - AVG(CASE WHEN q.qtr_no < t.latest_qtr_no THEN q.ROE END) AS roe_improvement
    FROM top_pb t
    JOIN quarter_fin q
      ON q.COMPANYCODE = t.COMPANYCODE
     AND q.qtr_no BETWEEN t.latest_qtr_no - 4 AND t.latest_qtr_no
    GROUP BY
        t.COMPANYCODE,
        t.INNERCODE,
        t.stock_code,
        t.stock_name,
        t.pb_rank,
        t.pb_total,
        t.PB,
        t.EPS,
        t.latest_roe,
        t.NEGOTIABLEMV,
        t.latest_end_date,
        t.latest_qtr_no
    HAVING COUNT(DISTINCT q.qtr_no) = 5
       AND SUM(q.qtr_no = t.latest_qtr_no) = 1
       AND SUM(q.qtr_no = t.latest_qtr_no - 1) = 1
       AND SUM(q.qtr_no = t.latest_qtr_no - 2) = 1
       AND SUM(q.qtr_no = t.latest_qtr_no - 3) = 1
       AND SUM(q.qtr_no = t.latest_qtr_no - 4) = 1
),
roe_ranked AS (
    SELECT
        rf.*,
        ROW_NUMBER() OVER (ORDER BY rf.roe_improvement DESC, rf.stock_code) AS roe_rank,
        COUNT(*) OVER () AS roe_total
    FROM roe_features rf
),
top_roe AS (
    SELECT *
    FROM roe_ranked
    WHERE roe_rank <= CEIL(roe_total * 0.10)
)
SELECT
    stock_code AS `股票代码`,
    stock_name AS `股票简称`,
    ROUND(PB, 2) AS `PB`,
    ROUND(EPS, 2) AS `EPS`,
    ROUND(latest_roe, 2) AS `最新ROE(%)`,
    ROUND(roe_improvement, 2) AS `ROE改善幅度(pct)`,
    ROUND(NEGOTIABLEMV / 100000000, 2) AS `流通市值（亿元）`
FROM top_roe
ORDER BY NEGOTIABLEMV ASC, stock_code
LIMIT 10;
