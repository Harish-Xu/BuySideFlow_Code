WITH
params AS (
    SELECT
        DATE('2023-04-28') AS as_of_date,
        DATE('2022-12-31') AS latest_annual_end
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
                         ls.JSID DESC,
                         ls.ID DESC
            ) AS rn
        FROM (
            SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID
            FROM lc_liststatus
            UNION ALL
            SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID
            FROM lc_stibliststatus
        ) ls
        JOIN params p
          ON ls.CHANGEDATE <= p.as_of_date
         AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
        WHERE ls.CHANGETYPE BETWEEN 1 AND 9
    ) ranked
    WHERE rn = 1
      AND CHANGETYPE IN (1, 3)
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
            LPAD(s.SECUCODE, 6, '0') AS SECUCODE,
            s.LISTEDSECTOR,
            COALESCE(NULLIF(s.SECUABBR, ''), NULLIF(s.CHINAMEABBR, ''), s.CHINAME, s.SECUCODE) AS company,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.SECUCODE, s.INNERCODE
            ) AS rn
        FROM secumain s
        JOIN list_status_asof ls
          ON ls.INNERCODE = s.INNERCODE
        JOIN params p
          ON s.LISTEDDATE <= p.as_of_date
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2)
          AND s.SECUCODE NOT LIKE 'X%'
    ) ranked
    WHERE rn = 1
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
                         e.INSERTTIME DESC,
                         e.JSID DESC,
                         e.ID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR <> 7
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
        UNION ALL
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.EFFECTIVEDATE DESC,
                         e.INFOPUBLDATE DESC,
                         e.INSERTTIME DESC,
                         e.JSID DESC,
                         e.ID DESC
            ) AS rn
        FROM lc_stibexgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR = 7
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND e.EFFECTIVEDATE <= p.as_of_date
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    ) t
    WHERE t.rn = 1
),
latest_valuation AS (
    SELECT
        sb.COMPANYCODE,
        v.PEG
    FROM (
        SELECT
            v.INNERCODE,
            v.PEG,
            ROW_NUMBER() OVER (
                PARTITION BY v.INNERCODE
                ORDER BY v.INSERTTIME DESC, v.JSID DESC, v.ID DESC
            ) AS rn
        FROM lc_dindicesforvaluation v
        JOIN stock_base sbf
          ON sbf.INNERCODE = v.INNERCODE
        JOIN params p
          ON v.TRADINGDAY = p.as_of_date
         AND (v.INSERTTIME IS NULL OR v.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
    ) v
    JOIN stock_base sb
      ON sb.INNERCODE = v.INNERCODE
    WHERE v.rn = 1
      AND v.PEG IS NOT NULL
),
nonstib_annual_fin AS (
    SELECT
        COMPANYCODE,
        ROE,
        NETPROFITGROWRATE3Y,
        DEBTASSETSRATIO
    FROM (
        SELECT
            m.COMPANYCODE,
            m.ROE,
            m.NPPCCGROWRATE3Y AS NETPROFITGROWRATE3Y,
            m.DEBTASSETSRATIO,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE
                ORDER BY m.INFOPUBLDATE DESC,
                         COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                         m.JSID DESC
            ) AS rn
        FROM lc_mainindexnew m
        JOIN stock_base sb
          ON sb.COMPANYCODE = m.COMPANYCODE
         AND sb.LISTEDSECTOR <> 7
        JOIN latest_valuation lv
          ON lv.COMPANYCODE = m.COMPANYCODE
        JOIN params p
          ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE m.ENDDATE = p.latest_annual_end
          AND MONTH(m.ENDDATE) = 12
    ) t
    WHERE rn = 1
      AND ROE IS NOT NULL
      AND NETPROFITGROWRATE3Y IS NOT NULL
      AND DEBTASSETSRATIO IS NOT NULL
),
stib_annual_fin AS (
    SELECT
        COMPANYCODE,
        ROE,
        NETPROFITGROWRATE3Y,
        DEBTASSETSRATIO
    FROM (
        SELECT
            m.COMPANYCODE,
            m.ROE,
            m.NPPCCGROWRATETHREEY AS NETPROFITGROWRATE3Y,
            m.DEBTASSETSRATIO,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE
                ORDER BY CASE WHEN m.IFADJUSTED = 1 THEN 0 ELSE 1 END,
                         m.INFOPUBLDATE DESC,
                         m.INSERTTIME DESC,
                         m.JSID DESC
            ) AS rn
        FROM lc_stibmainindex m
        JOIN stock_base sb
          ON sb.COMPANYCODE = m.COMPANYCODE
         AND sb.LISTEDSECTOR = 7
        JOIN latest_valuation lv
          ON lv.COMPANYCODE = m.COMPANYCODE
        JOIN params p
          ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE m.ENDDATE = p.latest_annual_end
          AND MONTH(m.ENDDATE) = 12
          AND m.IFMERGED = 1
    ) t
    WHERE rn = 1
      AND ROE IS NOT NULL
      AND NETPROFITGROWRATE3Y IS NOT NULL
      AND DEBTASSETSRATIO IS NOT NULL
),
latest_annual_fin AS (
    SELECT * FROM nonstib_annual_fin
    UNION ALL
    SELECT * FROM stib_annual_fin
)
SELECT
    sb.SECUCODE AS `股票代码`,
    sb.company AS `股票简称`,
    ROUND(lv.PEG, 4) AS `PEG`,
    ROUND(lf.ROE, 4) AS `ROE`,
    ROUND(lf.NETPROFITGROWRATE3Y, 4) AS `归母净利润三年复合增速`,
    ROUND(lf.DEBTASSETSRATIO, 4) AS `资产负债率`,
    ia.FIRSTINDUSTRYNAME AS `行业`
FROM stock_base sb
JOIN latest_valuation lv
  ON lv.COMPANYCODE = sb.COMPANYCODE
JOIN latest_annual_fin lf
  ON lf.COMPANYCODE = sb.COMPANYCODE
JOIN industry_asof ia
  ON ia.COMPANYCODE = sb.COMPANYCODE
WHERE lf.ROE > 15
  AND lf.NETPROFITGROWRATE3Y BETWEEN 15 AND 30
  AND lf.DEBTASSETSRATIO < 55
ORDER BY lv.PEG ASC,
         sb.SECUCODE ASC
LIMIT 10;
