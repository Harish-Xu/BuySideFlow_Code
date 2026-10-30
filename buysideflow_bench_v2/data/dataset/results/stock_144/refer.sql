WITH params AS (
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
        FROM (
            SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID, SECUMARKET
            FROM lc_liststatus
            UNION ALL
            SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID, 83 AS SECUMARKET
            FROM lc_stibliststatus
        ) ls
        JOIN params p
          ON ls.CHANGEDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
        WHERE ls.SECUMARKET IN (83, 90, 18)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
stock_base AS (
    SELECT *
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUCODE,
            s.LISTEDSECTOR,
            COALESCE(s.CHINAMEABBR, s.SECUABBR, s.CHINAME, s.SECUCODE) AS company_name,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.INNERCODE
            ) AS rn
        FROM secumain s
        JOIN params p
          ON s.LISTEDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        LEFT JOIN listed_asof la
          ON la.INNERCODE = s.INNERCODE
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90, 18)
          AND s.LISTEDSECTOR IN (1, 2, 6, 7, 8)
          AND la.CHANGETYPE IN (1, 3)
    ) t
    WHERE rn = 1
),
industry_asof AS (
    SELECT COMPANYCODE, first_industry
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME AS first_industry,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR <> 7
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_date
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)

        UNION ALL

        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME AS first_industry,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
            ) AS rn
        FROM lc_stibexgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR = 7
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_date
        WHERE e.STANDARD = 38
          AND e.EFFECTIVEDATE <= p.as_of_date
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    ) t
    WHERE rn = 1
),
base_companies AS (
    SELECT
        sb.INNERCODE,
        sb.COMPANYCODE,
        sb.SECUCODE,
        sb.company_name,
        sb.LISTEDSECTOR
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    WHERE ia.first_industry NOT IN ('非银金融', '银行')
      AND NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          JOIN params p
            ON 1 = 1
          WHERE ra.INNERCODE = sb.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND ra.IMPLEMENTDATE <= p.as_of_date
            AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
            AND (
                ra.REMOVEDATE IS NULL
                OR ra.REMOVEDATE > p.as_of_date
                OR ra.REMOVEINFOPUBLDATE > p.as_of_date
            )
      )
),
annual_nonstib AS (
    SELECT
        md.COMPANYCODE,
        md.ENDDATE,
        md.NPFROMPARENTCOMPANYOWNERS AS parent_net_profit,
        ROW_NUMBER() OVER (
            PARTITION BY md.COMPANYCODE, md.ENDDATE
            ORDER BY CASE md.MARK
                         WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                         WHEN 6 THEN 4 WHEN 2 THEN 5 ELSE 99
                     END,
                     CASE WHEN md.ACCOUNTINGSTANDARDS = 1 THEN 0 ELSE 1 END,
                     md.INFOPUBLDATE DESC,
                     md.INSERTTIME DESC,
                     md.JSID DESC
        ) AS rn
    FROM lc_maindatanew md
    JOIN base_companies bc
      ON bc.COMPANYCODE = md.COMPANYCODE
     AND bc.LISTEDSECTOR <> 7
    JOIN params p
      ON md.INFOPUBLDATE <= p.as_of_date
    WHERE md.ENDDATE IN (DATE('2022-12-31'), DATE('2024-12-31'))
      AND md.NPFROMPARENTCOMPANYOWNERS IS NOT NULL
),
annual_stib AS (
    SELECT
        md.COMPANYCODE,
        md.ENDDATE,
        md.NPPARENTCOMPANYOWNERS AS parent_net_profit,
        ROW_NUMBER() OVER (
            PARTITION BY md.COMPANYCODE, md.ENDDATE
            ORDER BY CASE WHEN md.IFMERGED = 1 THEN 0 ELSE 1 END,
                     CASE WHEN md.IFADJUSTED = 1 THEN 0 ELSE 1 END,
                     md.INFOPUBLDATE DESC,
                     md.INSERTTIME DESC,
                     md.JSID DESC
        ) AS rn
    FROM lc_stibmainindex md
    JOIN base_companies bc
      ON bc.COMPANYCODE = md.COMPANYCODE
     AND bc.LISTEDSECTOR = 7
    JOIN params p
      ON md.INFOPUBLDATE <= p.as_of_date
    WHERE md.ENDDATE IN (DATE('2022-12-31'), DATE('2024-12-31'))
      AND md.NPPARENTCOMPANYOWNERS IS NOT NULL
),
annual_reports AS (
    SELECT COMPANYCODE, ENDDATE, parent_net_profit
    FROM annual_nonstib WHERE rn = 1
    UNION ALL
    SELECT COMPANYCODE, ENDDATE, parent_net_profit
    FROM annual_stib WHERE rn = 1
),
annual_pass AS (
    SELECT
        ar.COMPANYCODE,
        2024 AS latest_year,
        MAX(CASE WHEN ar.ENDDATE = DATE('2024-12-31') THEN ar.parent_net_profit END) AS latest_profit,
        (POWER(
            MAX(CASE WHEN ar.ENDDATE = DATE('2024-12-31') THEN ar.parent_net_profit END)
            / NULLIF(MAX(CASE WHEN ar.ENDDATE = DATE('2022-12-31') THEN ar.parent_net_profit END), 0),
            1.0 / 2
        ) - 1) * 100 AS cagr_2y_pct
    FROM annual_reports ar
    GROUP BY ar.COMPANYCODE
    HAVING MAX(CASE WHEN ar.ENDDATE = DATE('2024-12-31') THEN ar.parent_net_profit END) > 0
       AND MAX(CASE WHEN ar.ENDDATE = DATE('2022-12-31') THEN ar.parent_net_profit END) > 0
       AND (POWER(
            MAX(CASE WHEN ar.ENDDATE = DATE('2024-12-31') THEN ar.parent_net_profit END)
            / NULLIF(MAX(CASE WHEN ar.ENDDATE = DATE('2022-12-31') THEN ar.parent_net_profit END), 0),
            1.0 / 2
        ) - 1) * 100 > 50
),
quarterly_reports AS (
    SELECT
        q.COMPANYCODE,
        q.ENDDATE,
        q.NPFROMPARENTCOMPANYOWNERS AS parent_net_profit,
        ROW_NUMBER() OVER (
            PARTITION BY q.COMPANYCODE, q.ENDDATE
            ORDER BY CASE q.MARK
                         WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                         WHEN 6 THEN 4 WHEN 2 THEN 5 ELSE 99
                     END,
                     q.INFOPUBLDATE DESC,
                     q.INSERTTIME DESC,
                     q.JSID DESC
        ) AS rn
    FROM lc_qfinancialindexnew q
    JOIN base_companies bc
      ON bc.COMPANYCODE = q.COMPANYCODE
    JOIN params p
      ON q.INFOPUBLDATE <= p.as_of_date
    WHERE q.ACCOUNTINGSTANDARDS = 1
      AND q.ENDDATE IN (
            DATE('2024-12-31'),
            DATE('2025-03-31'),
            DATE('2025-06-30'),
            DATE('2025-09-30')
      )
      AND q.NPFROMPARENTCOMPANYOWNERS IS NOT NULL
),
quarter_pass AS (
    SELECT
        qr.COMPANYCODE,
        DATE('2025-09-30') AS latest_quarter_enddate,
        MAX(CASE WHEN qr.ENDDATE = DATE('2025-09-30') THEN qr.parent_net_profit END) AS latest_quarter_profit
    FROM quarterly_reports qr
    WHERE qr.rn = 1
    GROUP BY qr.COMPANYCODE
    HAVING MAX(CASE WHEN qr.ENDDATE = DATE('2025-09-30') THEN qr.parent_net_profit END) >= 200000000
       AND MAX(CASE WHEN qr.ENDDATE = DATE('2025-09-30') THEN qr.parent_net_profit END)
           > MAX(CASE WHEN qr.ENDDATE = DATE('2025-06-30') THEN qr.parent_net_profit END)
       AND MAX(CASE WHEN qr.ENDDATE = DATE('2025-06-30') THEN qr.parent_net_profit END)
           > MAX(CASE WHEN qr.ENDDATE = DATE('2025-03-31') THEN qr.parent_net_profit END)
       AND MAX(CASE WHEN qr.ENDDATE = DATE('2025-03-31') THEN qr.parent_net_profit END)
           > MAX(CASE WHEN qr.ENDDATE = DATE('2024-12-31') THEN qr.parent_net_profit END)
)
SELECT
    bc.SECUCODE AS `证券代码`,
    bc.company_name AS `公司简称`,
    ap.latest_year AS `最新年度`,
    ROUND(ap.latest_profit / 100000000, 2) AS `最近一年度归母净利润(亿元)`,
    ROUND(ap.cagr_2y_pct, 4) AS `两年复合增速(%)`,
    DATE_FORMAT(qp.latest_quarter_enddate, '%Y-%m-%d') AS `最新季度报告期`,
    ROUND(qp.latest_quarter_profit / 100000000, 2) AS `最新季度归母净利润(亿元)`
FROM annual_pass ap
JOIN quarter_pass qp
  ON qp.COMPANYCODE = ap.COMPANYCODE
JOIN base_companies bc
  ON bc.COMPANYCODE = ap.COMPANYCODE
ORDER BY qp.latest_quarter_profit DESC, bc.SECUCODE;
