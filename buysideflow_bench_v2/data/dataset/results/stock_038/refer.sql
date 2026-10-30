WITH
params AS (
    SELECT
        DATE('2025-04-29') AS as_of_date,
        DATE('2024-12-31') AS annual_end
),
target_index AS (
    SELECT
        s.INNERCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS index_name,
        s.SECUCODE
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE = '000852'
      AND s.SECUMARKET = 83
),
index_component AS (
    SELECT DISTINCT
        ti.index_name,
        ti.SECUCODE AS index_code,
        sm.COMPANYCODE
    FROM target_index ti
    JOIN lc_indexcomponent ic
      ON ic.INDEXINNERCODE = ti.INNERCODE
    JOIN secumain sm
      ON sm.INNERCODE = ic.SECUINNERCODE
    JOIN params p
      ON ic.INDATE <= p.as_of_date
    WHERE sm.COMPANYCODE IS NOT NULL
      AND (ic.OUTDATE IS NULL OR ic.OUTDATE > p.as_of_date)
),
component_company AS (
    SELECT DISTINCT COMPANYCODE
    FROM index_component
),
deriv_ver AS (
    SELECT
        d.COMPANYCODE,
        DATE(d.ENDDATE) AS end_date,
        d.ASSETIMPAIRMENTLOSSTTM,
        ROW_NUMBER() OVER (
            PARTITION BY d.COMPANYCODE, DATE(d.ENDDATE)
            ORDER BY d.INFOPUBLDATE DESC, d.JSID DESC, d.ID DESC
        ) AS rn_ver
    FROM lc_derivativedata d
    JOIN component_company c
      ON c.COMPANYCODE = d.COMPANYCODE
    JOIN params p
      ON d.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE d.ENDDATE <= p.as_of_date
),
latest_deriv AS (
    SELECT
        COMPANYCODE,
        end_date,
        ASSETIMPAIRMENTLOSSTTM
    FROM (
        SELECT
            COMPANYCODE,
            end_date,
            ASSETIMPAIRMENTLOSSTTM,
            ROW_NUMBER() OVER (
                PARTITION BY COMPANYCODE
                ORDER BY end_date DESC
            ) AS rn_desc
        FROM deriv_ver
        WHERE rn_ver = 1
    ) t
    WHERE rn_desc = 1
),
annual_profit AS (
    SELECT
        COMPANYCODE,
        NPFROMPARENTCOMPANYOWNERS AS annual_profit
    FROM (
        SELECT
            m.COMPANYCODE,
            m.NPFROMPARENTCOMPANYOWNERS,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
                ORDER BY m.INFOPUBLDATE DESC,
                         CASE m.MARK WHEN 1 THEN 1 WHEN 8 THEN 2
                              WHEN 7 THEN 3 WHEN 6 THEN 4 WHEN 2 THEN 5 END,
                         m.INSERTTIME DESC,
                         m.JSID DESC, m.ID DESC
            ) AS rn_desc
        FROM lc_maindatanew m
        JOIN component_company c ON c.COMPANYCODE = m.COMPANYCODE
        JOIN params p
          ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE DATE(m.ENDDATE) = p.annual_end
          AND m.INFOSOURCECODE IN (110101, 120102)
          AND m.MARK IN (1, 8, 7, 6, 2)
    ) t
    WHERE rn_desc = 1
),
annual_balance AS (
    SELECT
        b.COMPANYCODE,
        DATE(b.ENDDATE) AS end_date,
        b.GOODWILL,
        b.SEWITHOUTMI,
        ROW_NUMBER() OVER (
            PARTITION BY b.COMPANYCODE, DATE(b.ENDDATE)
            ORDER BY b.INFOPUBLDATE DESC,
                     b.INSERTTIME DESC,
                     b.JSID DESC,
                     b.ID DESC
        ) AS rn_ver
    FROM lc_balancesheetall b
    JOIN component_company c
      ON c.COMPANYCODE = b.COMPANYCODE
    JOIN params p
      ON b.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE DATE(b.ENDDATE) = p.annual_end
      AND b.IFMERGED = 1
      AND b.IFCOMPLETE = 1
),
annual_goodwill AS (
    SELECT
        COMPANYCODE,
        GOODWILL,
        SEWITHOUTMI
    FROM annual_balance
    WHERE rn_ver = 1
),
risk_flag AS (
    SELECT
        ic.index_name,
        ic.index_code,
        ic.COMPANYCODE,
        CASE
            WHEN ld.ASSETIMPAIRMENTLOSSTTM IS NOT NULL
             AND ap.annual_profit > 0
            THEN (ld.ASSETIMPAIRMENTLOSSTTM > ap.annual_profit * 0.8)
            ELSE NULL
        END AS asset_impair_flag,
        CASE
            WHEN lg.GOODWILL IS NOT NULL
             AND lg.SEWITHOUTMI IS NOT NULL
             AND lg.SEWITHOUTMI > 0
            THEN (lg.GOODWILL > lg.SEWITHOUTMI * 0.3)
            ELSE NULL
        END AS goodwill_risk_flag
    FROM index_component ic
    LEFT JOIN latest_deriv ld
      ON ld.COMPANYCODE = ic.COMPANYCODE
    LEFT JOIN annual_profit ap
      ON ap.COMPANYCODE = ic.COMPANYCODE
    LEFT JOIN annual_goodwill lg
      ON lg.COMPANYCODE = ic.COMPANYCODE
)
SELECT
    index_name AS `指数`,
    COUNT(*) AS `成分公司数`,
    SUM(CASE WHEN asset_impair_flag = 1 OR goodwill_risk_flag = 1
                  OR (asset_impair_flag = 0 AND goodwill_risk_flag = 0)
             THEN 1 ELSE 0 END) AS `综合可判定公司数`,
    SUM(CASE WHEN asset_impair_flag = 1 OR goodwill_risk_flag = 1
             THEN 1 ELSE 0 END) AS `综合风险公司数`
FROM risk_flag
GROUP BY index_name, index_code
ORDER BY index_code;
