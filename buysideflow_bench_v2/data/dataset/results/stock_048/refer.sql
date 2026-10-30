WITH
params AS (
    SELECT
        DATE('2025-11-28') AS as_of_date,
        DATE('2025-09-30') AS latest_quarter_end
),
trade_asof AS (
    SELECT MAX(t.TRADINGDATE) AS trade_date
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE < p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET IN (83, 90, 18)
),
stock_base AS (
    SELECT
        INNERCODE,
        COMPANYCODE,
        SECUCODE,
        LISTEDSECTOR,
        company
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUCODE,
            s.LISTEDSECTOR,
            COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS company,
            ROW_NUMBER() OVER (PARTITION BY s.COMPANYCODE ORDER BY s.INNERCODE) AS rn
        FROM secumain s
        JOIN trade_asof ta
          ON 1 = 1
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90, 18)
          AND s.LISTEDSECTOR IN (1, 2, 6, 7, 8)
          AND EXISTS (
              SELECT 1
              FROM lc_dindicesforvaluation v
              WHERE v.INNERCODE = s.INNERCODE
                AND v.TRADINGDAY = ta.trade_date
                AND v.PE > 0
          )
    ) t
    WHERE rn = 1
),
latest_single_quarter AS (
    SELECT
        COMPANYCODE,
        NETPROFITYOY
    FROM (
        SELECT
            q.COMPANYCODE,
            q.NETPROFITYOY,
            ROW_NUMBER() OVER (
                PARTITION BY q.COMPANYCODE
                ORDER BY q.ENDDATE DESC,
                         q.INFOPUBLDATE DESC,
                         COALESCE(q.INSERTTIME, q.INFOPUBLDATE) DESC,
                         q.JSID DESC
            ) AS rn
        FROM lc_qfinancialindexnew q
        JOIN stock_base sb
          ON sb.COMPANYCODE = q.COMPANYCODE
        JOIN params p
          ON q.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(q.INSERTTIME, q.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE q.MARK = 2
          AND q.ACCOUNTINGSTANDARDS = 1
          AND q.ENDDATE <= p.latest_quarter_end
          AND q.NETPROFITYOY IS NOT NULL
    ) t
    WHERE rn = 1
),
nonstib_latest_roe_ttm AS (
    SELECT
        COMPANYCODE,
        ROETTM
    FROM (
        SELECT
            m.COMPANYCODE,
            m.ROETTM,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE
                ORDER BY m.ENDDATE DESC,
                         m.INFOPUBLDATE DESC,
                         COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                         m.JSID DESC
            ) AS rn
        FROM lc_mainindexnew m
        JOIN stock_base sb
          ON sb.COMPANYCODE = m.COMPANYCODE
         AND sb.LISTEDSECTOR <> 7
    JOIN params p
      ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
     AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE m.ENDDATE <= p.latest_quarter_end
          AND m.ROETTM IS NOT NULL
    ) t
    WHERE rn = 1
),
stib_latest_roe_ttm AS (
    SELECT
        COMPANYCODE,
        ROETTM
    FROM (
        SELECT
            m.COMPANYCODE,
            m.ROETTM,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE
                ORDER BY m.ENDDATE DESC,
                         CASE WHEN m.IFADJUSTED = 1 THEN 0 ELSE 1 END,
                         m.INFOPUBLDATE DESC, m.JSID DESC
            ) AS rn
        FROM lc_stibmainindex m
        JOIN stock_base sb
          ON sb.COMPANYCODE = m.COMPANYCODE
         AND sb.LISTEDSECTOR = 7
        JOIN params p
          ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE m.ENDDATE <= p.latest_quarter_end
          AND m.IFMERGED = 1
          AND m.ROETTM IS NOT NULL
    ) t
    WHERE rn = 1
),
latest_roe_ttm AS (
    SELECT * FROM nonstib_latest_roe_ttm
    UNION ALL
    SELECT * FROM stib_latest_roe_ttm
),
latest_pe AS (
    SELECT
        sb.COMPANYCODE,
        v.PE AS pe_ttm
    FROM (
        SELECT
            v.INNERCODE,
            MAX(v.JSID) AS max_jsid
        FROM lc_dindicesforvaluation v
        JOIN trade_asof ta
          ON v.TRADINGDAY = ta.trade_date
        JOIN stock_base sbf
          ON sbf.INNERCODE = v.INNERCODE
        WHERE v.PE IS NOT NULL
          AND v.PE > 0
        GROUP BY v.INNERCODE
    ) k
    JOIN lc_dindicesforvaluation v
      ON v.INNERCODE = k.INNERCODE
     AND v.JSID = k.max_jsid
    JOIN stock_base sb
      ON sb.INNERCODE = k.INNERCODE
),
nonstib_annual_roe AS (
    SELECT
        COMPANYCODE,
        report_year,
        ROEAVG
    FROM (
        SELECT
            m.COMPANYCODE,
            YEAR(m.ENDDATE) AS report_year,
            m.ROEAVG,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE, YEAR(m.ENDDATE)
                ORDER BY m.INFOPUBLDATE DESC,
                         COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                         m.JSID DESC
            ) AS rn
        FROM lc_mainindexnew m
        JOIN stock_base sb
          ON sb.COMPANYCODE = m.COMPANYCODE
         AND sb.LISTEDSECTOR <> 7
        JOIN params p
          ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE MONTH(m.ENDDATE) = 12
          AND m.ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
          AND m.ROEAVG IS NOT NULL
    ) t
    WHERE t.rn = 1
),
stib_annual_roe AS (
    SELECT
        COMPANYCODE,
        report_year,
        ROEAVG
    FROM (
        SELECT
            m.COMPANYCODE,
            YEAR(m.ENDDATE) AS report_year,
            m.ROEAVG,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE, YEAR(m.ENDDATE)
                ORDER BY CASE WHEN m.IFADJUSTED = 1 THEN 0 ELSE 1 END,
                         m.INFOPUBLDATE DESC, m.JSID DESC
            ) AS rn
        FROM lc_stibmainindex m
        JOIN stock_base sb
          ON sb.COMPANYCODE = m.COMPANYCODE
         AND sb.LISTEDSECTOR = 7
        JOIN params p
          ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE MONTH(m.ENDDATE) = 12
          AND m.ENDDATE IN (DATE('2022-12-31'), DATE('2023-12-31'), DATE('2024-12-31'))
          AND m.IFMERGED = 1
          AND m.ROEAVG IS NOT NULL
    ) t
    WHERE t.rn = 1
),
annual_roe_raw AS (
    SELECT * FROM nonstib_annual_roe
    UNION ALL
    SELECT * FROM stib_annual_roe
),
annual_roe AS (
    SELECT
        COMPANYCODE,
        STDDEV_POP(ROEAVG) AS roe_std_3y
    FROM annual_roe_raw
    GROUP BY COMPANYCODE
    HAVING COUNT(*) = 3
)
SELECT
    sb.SECUCODE AS `代码`,
    sb.company AS `公司`,
    ROUND(lsq.NETPROFITYOY, 4) AS `单季净利润同比增速`,
    ROUND(lr.ROETTM, 4) AS `TTM ROE`,
    ROUND(lp.pe_ttm, 4) AS `PE`,
    ROUND(ar.roe_std_3y, 4) AS `近三年ROE波动`
FROM stock_base sb
JOIN latest_single_quarter lsq
  ON lsq.COMPANYCODE = sb.COMPANYCODE
JOIN latest_roe_ttm lr
  ON lr.COMPANYCODE = sb.COMPANYCODE
JOIN latest_pe lp
  ON lp.COMPANYCODE = sb.COMPANYCODE
JOIN annual_roe ar
  ON ar.COMPANYCODE = sb.COMPANYCODE
WHERE lsq.NETPROFITYOY > 30
  AND lr.ROETTM > 15
  AND lp.pe_ttm < 25
  AND ar.roe_std_3y < 2
ORDER BY lsq.NETPROFITYOY DESC, `代码`
LIMIT 15;
