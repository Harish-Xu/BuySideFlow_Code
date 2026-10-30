WITH
params AS (SELECT DATE('2025-06-09') AS as_of_date),
latest_td AS (
    SELECT t.SECUMARKET, MAX(t.TRADINGDATE) AS trading_day
    FROM qt_tradingdaynew t
    JOIN params p ON t.TRADINGDATE < p.as_of_date
    WHERE t.SECUMARKET IN (83, 90)
      AND t.IFTRADINGDAY = 1
    GROUP BY t.SECUMARKET
),
stock_base AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        s.SECUMARKET,
        s.LISTEDSECTOR,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name
    FROM secumain s
    JOIN params p ON s.LISTEDDATE <= p.as_of_date
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR IN (1, 2)
      AND s.COMPANYCODE IS NOT NULL
),
stock_companies AS (
    SELECT DISTINCT COMPANYCODE FROM stock_base
),
industry_records AS (
    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYNAME,
        e.INFOPUBLDATE,
        e.INSERTTIME,
        e.JSID,
        0 AS source_priority
    FROM lc_exgindustry e
    JOIN stock_companies s ON s.COMPANYCODE = e.COMPANYCODE
    JOIN params p ON e.INFOPUBLDATE < p.as_of_date
    WHERE e.STANDARD = 38
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
      AND e.FIRSTINDUSTRYNAME IS NOT NULL
    UNION ALL
    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYNAME,
        e.INFOPUBLDATE,
        e.INSERTTIME,
        e.JSID,
        1 AS source_priority
    FROM lc_stibexgindustry e
    JOIN stock_companies s ON s.COMPANYCODE = e.COMPANYCODE
    JOIN params p
      ON e.EFFECTIVEDATE <= p.as_of_date
     AND e.INFOPUBLDATE < p.as_of_date
    WHERE e.STANDARD = 38
      AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
      AND e.FIRSTINDUSTRYNAME IS NOT NULL
),
industry_latest AS (
    SELECT
        e.COMPANYCODE,
        e.FIRSTINDUSTRYNAME,
        ROW_NUMBER() OVER (
            PARTITION BY e.COMPANYCODE
            ORDER BY e.INFOPUBLDATE DESC,
                     e.INSERTTIME DESC,
                     e.source_priority,
                     e.JSID DESC
        ) AS rn
    FROM industry_records e
),
listed_records AS (
    SELECT ls.INNERCODE, ls.CHANGETYPE, ls.CHANGEDATE,
           ls.INSERTTIME, ls.JSID, ls.ID, 0 AS source_priority
    FROM lc_liststatus ls
    JOIN stock_base s ON s.INNERCODE = ls.INNERCODE
    JOIN params p ON ls.CHANGEDATE <= p.as_of_date
                 AND ls.INSERTTIME < p.as_of_date
    WHERE ls.SECUMARKET IN (83, 90)
      AND ls.CHANGETYPE BETWEEN 1 AND 9
    UNION ALL
    SELECT ls.INNERCODE, ls.CHANGETYPE, ls.CHANGEDATE,
           ls.INSERTTIME, ls.JSID, ls.ID, 1 AS source_priority
    FROM lc_stibliststatus ls
    JOIN stock_base s ON s.INNERCODE = ls.INNERCODE
    JOIN params p ON ls.CHANGEDATE <= p.as_of_date
                 AND ls.INSERTTIME < p.as_of_date
    WHERE ls.CHANGETYPE BETWEEN 1 AND 9
),
listed_ranked AS (
    SELECT
        ls.INNERCODE,
        ls.CHANGETYPE,
        ROW_NUMBER() OVER (
            PARTITION BY ls.INNERCODE
            ORDER BY ls.CHANGEDATE DESC, ls.INSERTTIME DESC,
                     ls.source_priority, ls.JSID DESC, ls.ID DESC
        ) AS rn
    FROM listed_records ls
),
stock_pool AS (
    SELECT
        INNERCODE,
        COMPANYCODE,
        SECUMARKET,
        LISTEDSECTOR,
        SECUCODE,
        stock_name
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUMARKET,
            s.LISTEDSECTOR,
            s.SECUCODE,
            s.stock_name,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY s.SECUMARKET, s.SECUCODE
            ) AS rn
        FROM stock_base s
        JOIN listed_ranked ls ON ls.INNERCODE = s.INNERCODE
                             AND ls.rn = 1
                             AND ls.CHANGETYPE IN (1, 3, 6)
        JOIN params p ON 1 = 1
        WHERE NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              WHERE ra.INNERCODE = s.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME) < p.as_of_date
                AND DATE(ra.IMPLEMENTDATE) <= p.as_of_date
                AND (
                    ra.REMOVEDATE IS NULL
                    OR DATE(ra.REMOVEDATE) > p.as_of_date
                    OR DATE(COALESCE(ra.REMOVEINFOPUBLDATE, ra.REMOVEDATE)) >= p.as_of_date
                )
          )
    ) t
    WHERE rn=1
),
valu AS (
    SELECT
        v.INNERCODE,
        v.PB
    FROM lc_dindicesforvaluation v
    JOIN stock_pool sp ON sp.INNERCODE = v.INNERCODE
    JOIN latest_td ltd ON ltd.SECUMARKET = sp.SECUMARKET
                      AND v.TRADINGDAY = ltd.trading_day
),
fin_dedup AS (
    SELECT
        f.COMPANYCODE,
        f.ENDDATE,
        f.INFOPUBLDATE,
        f.MARK,
        f.TOTALASSETGROWRATE,
        f.OPERATINGREVENUEYOY,
        ROW_NUMBER() OVER (
            PARTITION BY f.COMPANYCODE, f.ENDDATE
            ORDER BY CASE f.MARK
                         WHEN 1 THEN 0
                         WHEN 8 THEN 1
                         WHEN 7 THEN 2
                         WHEN 6 THEN 3
                         WHEN 2 THEN 4
                         ELSE 9
                     END,
                     f.INFOPUBLDATE DESC, f.JSID DESC
        ) AS rn
    FROM fin_derivative f
    JOIN stock_pool sp ON sp.COMPANYCODE = f.COMPANYCODE
    JOIN params p ON f.INFOPUBLDATE < p.as_of_date
    WHERE f.ENDDATE >= DATE('2025-01-01')
      AND f.ENDDATE < p.as_of_date
      AND MONTH(f.ENDDATE) IN (3,6,9,12)
),
fin_q AS (
    SELECT
        COMPANYCODE,
        DATE(ENDDATE) AS end_date,
        TOTALASSETGROWRATE,
        OPERATINGREVENUEYOY
    FROM fin_dedup
    WHERE rn=1
),
latest_common_enddate AS (
    SELECT MAX(end_date) AS latest_end_date
    FROM fin_q
),
fin_latest AS (
    SELECT
        f.COMPANYCODE,
        f.TOTALASSETGROWRATE,
        f.OPERATINGREVENUEYOY
    FROM fin_q f
    JOIN latest_common_enddate d
      ON f.end_date = d.latest_end_date
),
eligible_panel AS (
    SELECT
        sp.INNERCODE,
        sp.SECUCODE,
        sp.LISTEDSECTOR,
        sp.stock_name,
        il.FIRSTINDUSTRYNAME AS industry,
        v.PB,
        fl.TOTALASSETGROWRATE AS asset_grow_pct,
        fl.OPERATINGREVENUEYOY AS rev_grow_pct
    FROM stock_pool sp
    JOIN industry_latest il
      ON sp.COMPANYCODE = il.COMPANYCODE
     AND il.rn=1
     AND il.FIRSTINDUSTRYNAME NOT IN ('银行', '非银金融', '房地产')
    LEFT JOIN valu v ON sp.INNERCODE = v.INNERCODE
    JOIN fin_latest fl ON sp.COMPANYCODE = fl.COMPANYCODE
    WHERE fl.TOTALASSETGROWRATE IS NOT NULL
      AND fl.OPERATINGREVENUEYOY IS NOT NULL
),
panel AS (
    SELECT * FROM eligible_panel WHERE PB IS NOT NULL
),
ranked_panel AS (
    SELECT
        p.*,
        PERCENT_RANK() OVER (PARTITION BY industry ORDER BY PB) AS pb_pct_rank,
        PERCENT_RANK() OVER (PARTITION BY industry ORDER BY asset_grow_pct) AS asset_pct_rank,
        PERCENT_RANK() OVER (PARTITION BY industry ORDER BY rev_grow_pct DESC) AS rev_pct_rank
    FROM panel p
)
SELECT
    p.SECUCODE AS 代码,
    p.stock_name AS 名称,
    p.industry AS 所属行业,
    ROUND(p.PB, 4) AS PB,
    ROUND(p.rev_grow_pct, 2) AS 营业收入同比增速,
    ROUND(p.asset_grow_pct, 2) AS 总资产同比增速
FROM ranked_panel p
WHERE p.pb_pct_rank <= 0.40
  AND p.asset_grow_pct > 0
  AND p.asset_pct_rank <= 0.40
  AND p.rev_pct_rank <= 0.30
ORDER BY p.SECUCODE;
