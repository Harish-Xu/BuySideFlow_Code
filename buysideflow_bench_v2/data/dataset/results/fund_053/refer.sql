WITH params AS (
    SELECT
        DATE('2025-06-30') AS report_date,
        DATE('2025-09-02') AS visible_before
),
fof AS (
    SELECT INNERCODE
    FROM mf_fundarchives
    WHERE SECUCODE = '501220'
    LIMIT 1
),
parent_pub AS (
    SELECT INNERCODE, MAX(INFOPUBLDATE) AS max_pub
    FROM mf_portfoliodetailsall
    WHERE INNERCODE = (SELECT INNERCODE FROM fof)
      AND ENDDATE = (SELECT report_date FROM params)
      AND REPORTTYPE = 6
      AND INVESTOBJECT IN (1, 6, 7)
      AND INFOPUBLDATE < (SELECT visible_before FROM params)
    GROUP BY INNERCODE
),
parent_pos AS (
    SELECT p.INVESTOBJECT, p.SECUINNERCODE, p.SECUCODE, p.RATIOINNV
    FROM mf_portfoliodetailsall p
    JOIN parent_pub pp
      ON pp.INNERCODE = p.INNERCODE
     AND pp.max_pub = p.INFOPUBLDATE
    WHERE p.ENDDATE = (SELECT report_date FROM params)
      AND p.REPORTTYPE = 6
      AND p.RATIOINNV IS NOT NULL
),
direct_stock AS (
    SELECT
        CASE
            WHEN COALESCE(sm.COMPANYCODE, hm.COMPANYCODE) IS NOT NULL
                THEN CONCAT('C:', COALESCE(sm.COMPANYCODE, hm.COMPANYCODE))
            ELSE CONCAT('S:', COALESCE(CAST(pp.SECUINNERCODE AS CHAR), pp.SECUCODE))
        END AS holding_key,
        COALESCE(sm.COMPANYCODE, hm.COMPANYCODE) AS COMPANYCODE,
        SUM(pp.RATIOINNV) AS abs_ratio
    FROM parent_pos pp
    LEFT JOIN secumain sm
      ON sm.INNERCODE = pp.SECUINNERCODE
    LEFT JOIN hk_secumain hm
      ON hm.INNERCODE = pp.SECUINNERCODE
    WHERE pp.INVESTOBJECT IN (1, 7)
    GROUP BY holding_key, COALESCE(sm.COMPANYCODE, hm.COMPANYCODE)
),
child_fund AS (
    SELECT SECUINNERCODE AS child_ic, SUM(RATIOINNV) AS fof_weight
    FROM parent_pos
    WHERE INVESTOBJECT = 6
      AND SECUINNERCODE IS NOT NULL
    GROUP BY SECUINNERCODE
),
child_pub AS (
    SELECT INNERCODE, MAX(INFOPUBLDATE) AS max_pub
    FROM mf_portfoliodetailsall
    WHERE INNERCODE IN (SELECT child_ic FROM child_fund)
      AND ENDDATE = (SELECT report_date FROM params)
      AND REPORTTYPE = 6
      AND INVESTOBJECT IN (1, 7)
      AND INFOPUBLDATE < (SELECT visible_before FROM params)
    GROUP BY INNERCODE
),
lookthrough_stock AS (
    SELECT
        CASE
            WHEN COALESCE(sm.COMPANYCODE, hm.COMPANYCODE) IS NOT NULL
                THEN CONCAT('C:', COALESCE(sm.COMPANYCODE, hm.COMPANYCODE))
            ELSE CONCAT('S:', COALESCE(CAST(p.SECUINNERCODE AS CHAR), p.SECUCODE))
        END AS holding_key,
        COALESCE(sm.COMPANYCODE, hm.COMPANYCODE) AS COMPANYCODE,
        SUM(cf.fof_weight * p.RATIOINNV) AS abs_ratio
    FROM child_fund cf
    JOIN child_pub cp
      ON cp.INNERCODE = cf.child_ic
    JOIN mf_portfoliodetailsall p
      ON p.INNERCODE = cf.child_ic
     AND p.INFOPUBLDATE = cp.max_pub
     AND p.ENDDATE = (SELECT report_date FROM params)
     AND p.REPORTTYPE = 6
     AND p.INVESTOBJECT IN (1, 7)
     AND p.RATIOINNV IS NOT NULL
    LEFT JOIN secumain sm
      ON sm.INNERCODE = p.SECUINNERCODE
    LEFT JOIN hk_secumain hm
      ON hm.INNERCODE = p.SECUINNERCODE
    GROUP BY holding_key, COALESCE(sm.COMPANYCODE, hm.COMPANYCODE)
),
stock_exposure AS (
    SELECT holding_key, COMPANYCODE, SUM(abs_ratio) AS abs_ratio
    FROM (
        SELECT holding_key, COMPANYCODE, abs_ratio FROM direct_stock
        UNION ALL
        SELECT holding_key, COMPANYCODE, abs_ratio FROM lookthrough_stock
    ) x
    GROUP BY holding_key, COMPANYCODE
),
exchange_industry AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME
    FROM (
        SELECT
            COMPANYCODE,
            FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY COMPANYCODE
                ORDER BY INFOPUBLDATE DESC, ID DESC
            ) AS rn
        FROM lc_exgindustry
        WHERE STANDARD = 38
          AND INFOPUBLDATE <= (SELECT report_date FROM params)
          AND (CANCELDATE IS NULL OR CANCELDATE > (SELECT report_date FROM params))
    ) x
    WHERE rn = 1
),
star_industry AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME
    FROM (
        SELECT
            COMPANYCODE,
            FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY COMPANYCODE
                ORDER BY EFFECTIVEDATE DESC, ID DESC
            ) AS rn
        FROM lc_stibexgindustry
        WHERE STANDARD = 38
          AND EFFECTIVEDATE <= (SELECT report_date FROM params)
          AND INFOPUBLDATE < (SELECT visible_before FROM params)
          AND (CANCELDATE IS NULL OR CANCELDATE > (SELECT report_date FROM params))
    ) x
    WHERE rn = 1
),
industry_exposure AS (
    SELECT
        COALESCE(e.FIRSTINDUSTRYNAME, s.FIRSTINDUSTRYNAME, '未映射') AS industry_name,
        SUM(se.abs_ratio) * 100 AS exposure_pct
    FROM stock_exposure se
    LEFT JOIN exchange_industry e
      ON e.COMPANYCODE = se.COMPANYCODE
    LEFT JOIN star_industry s
      ON s.COMPANYCODE = se.COMPANYCODE
    GROUP BY COALESCE(e.FIRSTINDUSTRYNAME, s.FIRSTINDUSTRYNAME, '未映射')
)
SELECT
    industry_name AS `申万一级行业名称`,
    ROUND(exposure_pct, 4) AS `暴露比例`
FROM industry_exposure
ORDER BY exposure_pct DESC, industry_name ASC;
