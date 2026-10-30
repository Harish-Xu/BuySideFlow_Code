WITH params AS (
    SELECT
        DATE('2025-12-31') AS as_of_date,
        TIMESTAMP('2025-12-31 23:59:59') AS as_of_dt
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
    SELECT INNERCODE
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY ls.CHANGEDATE DESC,
                         COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
                         ls.JSID DESC
            ) AS rn
        FROM lc_liststatus ls
        JOIN params p
          ON ls.CHANGEDATE <= p.as_of_date
         AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) <= p.as_of_dt
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
      AND CHANGETYPE IN (1, 3)
),
stock_base AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        s.SECUCODE,
        COALESCE(s.CHINAMEABBR, s.SECUABBR, s.CHINAME, s.SECUCODE) AS company
    FROM (
        SELECT
            sm.*,
            ROW_NUMBER() OVER (
                PARTITION BY sm.COMPANYCODE
                ORDER BY
                    CASE WHEN sm.LISTEDSECTOR = 1 THEN 0 ELSE 1 END,
                    sm.INNERCODE
            ) AS rn
        FROM secumain sm
        JOIN list_status_asof lsa
          ON lsa.INNERCODE = sm.INNERCODE
        CROSS JOIN trade_asof ta
        CROSS JOIN params p
        WHERE sm.SECUCATEGORY = 1
          AND sm.SECUMARKET IN (83, 90)
          AND sm.LISTEDSECTOR IN (1, 2, 6)
          AND sm.LISTEDDATE <= DATE_SUB(ta.trade_day, INTERVAL 1 YEAR)
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              WHERE ra.INNERCODE = sm.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND DATE(ra.IMPLEMENTDATE) <= ta.trade_day
                AND COALESCE(
                    ra.IMPLANNOUCEDATE,
                    ra.INSERTTIME,
                    ra.IMPLEMENTDATE
                ) <= p.as_of_dt
                AND (
                    ra.REMOVEDATE IS NULL
                    OR DATE(ra.REMOVEDATE) > ta.trade_day
                    OR COALESCE(
                        ra.REMOVEINFOPUBLDATE,
                        ra.INSERTTIME,
                        ra.REMOVEDATE
                    ) > p.as_of_dt
                )
          )
    ) s
    WHERE s.rn = 1
),
latest_financial_raw AS (
    SELECT
        m.COMPANYCODE,
        m.OPERATINGREVENUEGROWRATE,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE
            ORDER BY m.ENDDATE DESC, m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn
    FROM lc_mainindexnew m
    JOIN stock_base sb
      ON sb.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
),
latest_financial AS (
    SELECT
        COMPANYCODE,
        OPERATINGREVENUEGROWRATE
    FROM latest_financial_raw
    WHERE rn = 1
      AND OPERATINGREVENUEGROWRATE IS NOT NULL
),
valuation_asof AS (
    SELECT
        sb.COMPANYCODE,
        v.PE,
        v.NEGOTIABLEMV
    FROM stock_base sb
    JOIN (
        SELECT
            d.INNERCODE,
            d.PE,
            d.NEGOTIABLEMV,
            ROW_NUMBER() OVER (
                PARTITION BY d.INNERCODE
                ORDER BY d.JSID DESC, d.ID DESC
            ) AS rn
        FROM lc_dindicesforvaluation d
        JOIN trade_asof ta
          ON d.TRADINGDAY = ta.trade_day
        WHERE d.PE IS NOT NULL
          AND d.NEGOTIABLEMV IS NOT NULL
    ) v
      ON v.INNERCODE = sb.INNERCODE
     AND v.rn = 1
),
industry_asof AS (
    SELECT
        COMPANYCODE,
        FIRSTINDUSTRYNAME
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
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    ) x
    WHERE rn = 1
),
filtered AS (
    SELECT
        sb.SECUCODE,
        sb.company,
        lf.OPERATINGREVENUEGROWRATE,
        va.PE,
        va.NEGOTIABLEMV,
        ia.FIRSTINDUSTRYNAME,
        ROW_NUMBER() OVER (
            PARTITION BY ia.FIRSTINDUSTRYNAME
            ORDER BY va.PE ASC, sb.SECUCODE
        ) AS industry_rank
    FROM stock_base sb
    JOIN latest_financial lf
      ON lf.COMPANYCODE = sb.COMPANYCODE
    JOIN valuation_asof va
      ON va.COMPANYCODE = sb.COMPANYCODE
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    WHERE lf.OPERATINGREVENUEGROWRATE > 15
      AND va.PE > 0
      AND va.PE <= 30
      AND va.NEGOTIABLEMV BETWEEN 2000000000 AND 30000000000
)
SELECT
    SECUCODE AS `股票代码`,
    company AS `股票简称`,
    ROUND(OPERATINGREVENUEGROWRATE, 2) AS `营业收入同比增速`,
    ROUND(PE, 2) AS `PE(TTM)`,
    ROUND(NEGOTIABLEMV / 100000000, 2) AS `流通市值`,
    FIRSTINDUSTRYNAME AS `行业`
FROM filtered
WHERE industry_rank <= 2
ORDER BY PE ASC, SECUCODE
LIMIT 20;
