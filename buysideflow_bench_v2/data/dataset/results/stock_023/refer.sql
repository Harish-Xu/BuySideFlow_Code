WITH
params AS (
    SELECT TIMESTAMP('2025-12-31 23:59:59') AS as_of_dt
),
asof_trade AS (
    SELECT MAX(v.TRADINGDAY) AS trade_day
    FROM lc_dindicesforvaluation v
    JOIN params p
      ON v.TRADINGDAY <= p.as_of_dt
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
                    DATE(COALESCE(ls.INSERTTIME, ls.CHANGEDATE)) DESC,
                    ls.JSID DESC,
                    ls.ID DESC
            ) AS rn
        FROM lc_liststatus ls
        JOIN params p
          ON 1 = 1
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
          AND ls.CHANGEDATE IS NOT NULL
          AND DATE(ls.CHANGEDATE) <= DATE(p.as_of_dt)
          AND DATE(COALESCE(ls.INSERTTIME, ls.CHANGEDATE)) <= DATE(p.as_of_dt)
    ) t
    WHERE rn = 1
),
stock_base AS (
    SELECT
        INNERCODE,
        COMPANYCODE,
        LPAD(SECUCODE, 6, '0') AS stock_code,
        stock_name
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUCODE,
            COALESCE(NULLIF(s.SECUABBR, ''), NULLIF(s.CHINAMEABBR, ''), s.CHINAME, s.SECUCODE) AS stock_name,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.LISTEDDATE DESC, s.SECUMARKET, s.LISTEDSECTOR, s.SECUCODE, s.INNERCODE
            ) AS rn
        FROM secumain s
        LEFT JOIN list_status_asof lsa
          ON lsa.INNERCODE = s.INNERCODE
        CROSS JOIN asof_trade a
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2, 6)
          AND lsa.change_type IN (1, 3)
          AND s.LISTEDDATE <= a.trade_day
          AND DATEDIFF(a.trade_day, s.LISTEDDATE) >= 365
    ) t
    WHERE rn = 1
),
valuation AS (
    SELECT
        INNERCODE,
        PE,
        DIVIDENDRATIO
    FROM (
        SELECT
            v.INNERCODE,
            v.PE,
            v.DIVIDENDRATIO,
            ROW_NUMBER() OVER (
                PARTITION BY v.INNERCODE
                ORDER BY v.JSID DESC, v.ID DESC
            ) AS rn
        FROM lc_dindicesforvaluation v
        JOIN stock_base sb
          ON sb.INNERCODE = v.INNERCODE
        JOIN asof_trade a
          ON v.TRADINGDAY = a.trade_day
        JOIN params p
          ON COALESCE(v.INSERTTIME, v.TRADINGDAY) <= p.as_of_dt
        WHERE v.PE IS NOT NULL
          AND v.PE > 0
          AND v.DIVIDENDRATIO IS NOT NULL
    ) t
    WHERE rn = 1
),
suspended AS (
    SELECT DISTINCT r.INNERCODE
    FROM lc_suspendresumption r
    JOIN asof_trade a
      ON DATE(r.SUSPENDDATE) <= a.trade_day
    JOIN params p
      ON COALESCE(r.INFOPUBLDATE, r.INSERTTIME, r.SUSPENDDATE) <= p.as_of_dt
    WHERE r.RESUMPTIONDATE IS NULL
       OR DATE(r.RESUMPTIONDATE) > a.trade_day
),
mainindex_ver AS (
    SELECT
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS end_date,
        m.INFOPUBLDATE,
        m.ROE,
        m.OPERATINGREVENUEGROWRATE,
        m.NPPARENTCOMPANYYOY,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC,
                     m.INSERTTIME DESC,
                     m.JSID DESC,
                     m.ID DESC
        ) AS rn_ver
    FROM lc_mainindexnew m
    JOIN stock_base sb
      ON sb.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.INFOPUBLDATE <= p.as_of_dt
     AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) <= p.as_of_dt
    WHERE m.ENDDATE <= p.as_of_dt
),
latest_fin AS (
    SELECT
        COMPANYCODE,
        end_date,
        ROE,
        OPERATINGREVENUEGROWRATE,
        NPPARENTCOMPANYYOY
    FROM (
        SELECT
            COMPANYCODE,
            end_date,
            ROE,
            OPERATINGREVENUEGROWRATE,
            NPPARENTCOMPANYYOY,
            ROW_NUMBER() OVER (
                PARTITION BY COMPANYCODE
                ORDER BY end_date DESC
            ) AS rn_company
        FROM mainindex_ver
        WHERE rn_ver = 1
    ) t
    WHERE rn_company = 1
),
eligible AS (
    SELECT
        sb.stock_code,
        sb.stock_name,
        v.PE,
        v.DIVIDENDRATIO,
        lf.ROE,
        lf.OPERATINGREVENUEGROWRATE,
        lf.NPPARENTCOMPANYYOY
    FROM stock_base sb
    JOIN valuation v
      ON v.INNERCODE = sb.INNERCODE
    JOIN latest_fin lf
      ON lf.COMPANYCODE = sb.COMPANYCODE
    LEFT JOIN suspended sus
      ON sus.INNERCODE = sb.INNERCODE
    CROSS JOIN asof_trade a
    WHERE NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = sb.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND DATE(COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE)) <= a.trade_day
            AND DATE(ra.IMPLEMENTDATE) <= a.trade_day
            AND (
                ra.REMOVEDATE IS NULL
                OR DATE(ra.REMOVEDATE) > a.trade_day
                OR DATE(COALESCE(ra.REMOVEINFOPUBLDATE, ra.REMOVEDATE)) > a.trade_day
            )
      )
      AND sus.INNERCODE IS NULL
      AND v.PE <= 25
      AND v.DIVIDENDRATIO > 0
      AND lf.ROE > 10
      AND lf.OPERATINGREVENUEGROWRATE > 5
      AND lf.NPPARENTCOMPANYYOY > 10
)
SELECT
    stock_code AS `股票代码`,
    stock_name AS `股票简称`,
    PE AS `PE`,
    DIVIDENDRATIO AS `股息率`,
    ROE AS `ROE`,
    OPERATINGREVENUEGROWRATE AS `营业收入同比增速`,
    NPPARENTCOMPANYYOY AS `归母净利润同比增速`
FROM eligible
ORDER BY `股息率` DESC, `股票代码` ASC
LIMIT 10;
