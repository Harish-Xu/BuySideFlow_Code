WITH
params AS (
    SELECT TIMESTAMP('2025-08-31 23:59:59') AS as_of_dt,
           DATE('2025-06-30') AS report_date
),
actual_date AS (
    SELECT MAX(DATE(t.TRADINGDATE)) AS trade_date
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= DATE(p.as_of_dt)
    WHERE t.SECUMARKET = 83
      AND t.IFTRADINGDAY = 1
),
calendar_20d AS (
    SELECT trade_date
    FROM (
        SELECT
            DATE(t.TRADINGDATE) AS trade_date,
            ROW_NUMBER() OVER (ORDER BY t.TRADINGDATE DESC) AS rn
        FROM qt_tradingdaynew t
        JOIN actual_date a
          ON t.TRADINGDATE <= a.trade_date
        WHERE t.SECUMARKET = 83
          AND t.IFTRADINGDAY = 1
    ) d
    WHERE rn <= 20
),
list_status_asof AS (
    SELECT INNERCODE, change_type
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE AS change_type,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY DATE(ls.CHANGEDATE) DESC,
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
        JOIN params p
          ON COALESCE(ls.INSERTTIME, ls.CHANGEDATE) <= p.as_of_dt
        JOIN actual_date a
          ON DATE(ls.CHANGEDATE) <= a.trade_date
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
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
          ON DATE(s.LISTEDDATE) <= DATE_SUB(a.trade_date, INTERVAL 2 YEAR)
        JOIN params p
          ON 1 = 1
        LEFT JOIN list_status_asof lsa
          ON lsa.INNERCODE = s.INNERCODE
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2, 6, 7)
          AND lsa.change_type IN (1, 3)
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              WHERE ra.INNERCODE = s.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) <= p.as_of_dt
                AND DATE(ra.IMPLEMENTDATE) <= a.trade_date
                AND (
                    ra.REMOVEDATE IS NULL
                    OR DATE(ra.REMOVEDATE) > a.trade_date
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
                         e.JSID DESC,
                         e.FIRSTINDUSTRYNAME
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR <> 7
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_dt
         AND COALESCE(e.INSERTTIME, e.INFOPUBLDATE) < DATE_ADD(DATE(p.as_of_dt), INTERVAL 1 DAY)
        JOIN actual_date a
          ON 1 = 1
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR DATE(e.CANCELDATE) > a.trade_date)

        UNION ALL

        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.EFFECTIVEDATE DESC,
                         e.INFOPUBLDATE DESC,
                         e.JSID DESC,
                         e.FIRSTINDUSTRYNAME
            ) AS rn
        FROM lc_stibexgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR = 7
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_dt
         AND COALESCE(e.INSERTTIME, e.INFOPUBLDATE) < DATE_ADD(DATE(p.as_of_dt), INTERVAL 1 DAY)
        JOIN actual_date a
          ON 1 = 1
        WHERE e.STANDARD = 38
          AND e.EFFECTIVEDATE <= a.trade_date
          AND (e.CANCELDATE IS NULL OR DATE(e.CANCELDATE) > a.trade_date)
    ) t
    WHERE rn = 1
),
industry_pool AS (
    SELECT
        sb.INNERCODE,
        sb.COMPANYCODE,
        sb.SECUCODE,
        sb.LISTEDSECTOR,
        sb.stock_name,
        ia.FIRSTINDUSTRYNAME
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    WHERE ia.FIRSTINDUSTRYNAME IN ('电子', '通信', '计算机')
),
liquidity AS (
    SELECT
        ip.INNERCODE,
        AVG(
            COALESCE(
                CASE
                    WHEN ip.LISTEDSECTOR = 7 THEN sq.TURNOVERVALUE
                    ELSE q.TURNOVERVALUE
                END,
                0
            )
        ) AS avg_turnover_20d
    FROM industry_pool ip
    CROSS JOIN calendar_20d c
    LEFT JOIN qt_dailyquote q
      ON q.INNERCODE = ip.INNERCODE
     AND ip.LISTEDSECTOR <> 7
     AND q.TRADINGDAY = c.trade_date
    LEFT JOIN lc_stibdailyquote sq
      ON sq.INNERCODE = ip.INNERCODE
     AND ip.LISTEDSECTOR = 7
     AND sq.TRADINGDAY = c.trade_date
    GROUP BY ip.INNERCODE
),
liquid_pool AS (
    SELECT ip.*
    FROM industry_pool ip
    JOIN liquidity l
      ON l.INNERCODE = ip.INNERCODE
    WHERE l.avg_turnover_20d >= 50000000
),
financial_ver AS (
    SELECT
        f.COMPANYCODE,
        f.RANDDEXPENSERATE,
        f.OPERATINGREVENUEYOY,
        f.NPPARENTCOMPANYCUTYOY,
        ROW_NUMBER() OVER (
            PARTITION BY f.COMPANYCODE, DATE(f.ENDDATE)
            ORDER BY CASE f.MARK
                         WHEN 1 THEN 1
                         WHEN 8 THEN 2
                         WHEN 7 THEN 3
                         WHEN 6 THEN 4
                         WHEN 2 THEN 5
                         ELSE 99
                     END,
                     f.INFOPUBLDATE DESC,
                     f.JSID DESC
        ) AS rn_ver
    FROM fin_derivative f
    JOIN liquid_pool lp
      ON lp.COMPANYCODE = f.COMPANYCODE
    JOIN params p
      ON DATE(f.ENDDATE) = p.report_date
     AND f.INFOPUBLDATE <= p.as_of_dt
     AND COALESCE(f.INSERTTIME, f.INFOPUBLDATE) < DATE_ADD(DATE(p.as_of_dt), INTERVAL 1 DAY)
    WHERE f.MARK IN (1, 8, 7, 6, 2)
),
financial AS (
    SELECT
        COMPANYCODE,
        RANDDEXPENSERATE,
        OPERATINGREVENUEYOY,
        NPPARENTCOMPANYCUTYOY
    FROM financial_ver
    WHERE rn_ver = 1
),
median_ranked AS (
    SELECT
        lp.FIRSTINDUSTRYNAME,
        lp.COMPANYCODE,
        f.RANDDEXPENSERATE,
        ROW_NUMBER() OVER (
            PARTITION BY lp.FIRSTINDUSTRYNAME
            ORDER BY f.RANDDEXPENSERATE, lp.COMPANYCODE
        ) AS rn,
        COUNT(*) OVER (
            PARTITION BY lp.FIRSTINDUSTRYNAME
        ) AS cnt
    FROM liquid_pool lp
    JOIN financial f
      ON f.COMPANYCODE = lp.COMPANYCODE
    WHERE f.RANDDEXPENSERATE IS NOT NULL
),
industry_median AS (
    SELECT
        FIRSTINDUSTRYNAME,
        AVG(RANDDEXPENSERATE) AS median_rd_rate
    FROM median_ranked
    WHERE rn IN (FLOOR((cnt + 1) / 2), FLOOR((cnt + 2) / 2))
    GROUP BY FIRSTINDUSTRYNAME
)
SELECT
    lp.SECUCODE AS `股票代码`,
    lp.stock_name AS `股票简称`,
    lp.FIRSTINDUSTRYNAME AS `申万一级行业`,
    ROUND(f.RANDDEXPENSERATE, 2) AS `研发费用率`,
    ROUND(f.OPERATINGREVENUEYOY, 2) AS `营业收入同比`,
    ROUND(f.NPPARENTCOMPANYCUTYOY, 2) AS `扣非归母净利润同比`
FROM liquid_pool lp
JOIN financial f
  ON f.COMPANYCODE = lp.COMPANYCODE
JOIN industry_median m
  ON m.FIRSTINDUSTRYNAME = lp.FIRSTINDUSTRYNAME
WHERE f.RANDDEXPENSERATE > m.median_rd_rate
  AND f.OPERATINGREVENUEYOY > 0
  AND f.NPPARENTCOMPANYCUTYOY > 0
ORDER BY f.NPPARENTCOMPANYCUTYOY DESC,
         lp.SECUCODE ASC
LIMIT 10;
