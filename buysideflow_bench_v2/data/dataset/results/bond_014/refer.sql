WITH params AS (
    SELECT DATE('2026-01-20') AS as_of_date
),
valid_default_events AS (
    SELECT
        CAST(bc.COMPANYCODE AS UNSIGNED) AS companycode,
        CAST(bc.MAINCODE AS UNSIGNED) AS maincode,
        CAST(bc.INNERCODE AS UNSIGNED) AS innercode,
        bc.SECUCODE AS bond_code,
        COALESCE(bc.SECUABBR, bc.CHINAMEABBR, bc.CHINAME) AS bond_name,
        bc.ISSUER AS issuer,
        DATE(d.DEFAULTDATE) AS default_date,
        DATE(d.INFOPUBLDATE) AS publish_date,
        d.DEFAULTPROG AS default_prog,
        d.DEFAULTTYPE AS default_type,
        d.ID AS default_id
    FROM bond_default d
    JOIN bond_code bc
        ON bc.INNERCODE = d.INNERCODE
    CROSS JOIN params p
    WHERE bc.COMPANYCODE IS NOT NULL
      AND d.DEFAULTDATE IS NOT NULL
      AND d.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      -- 境内债市场：上交所、银行间债券市场、深交所。
      AND bc.SECUMARKET IN (83, 89, 90)
      -- 新增违约/展期主体口径：排除 DEFAULTTYPE=1002 的信用评级类预计违约，
      -- 保留实质违约、技术性违约、本息展期、交叉违约和实质违约进度记录。
      AND COALESCE(d.DEFAULTTYPE, -1) <> 1002
      AND (
          d.DEFAULTTYPE IN (2001, 2002, 2003, 100301)
          OR d.DEFAULTPROG IN (201, 202, 203, 801)
      )
),
first_subject_default AS (
    SELECT *
    FROM (
        SELECT
            e.*,
            ROW_NUMBER() OVER (
                PARTITION BY e.companycode
                ORDER BY e.default_date, e.publish_date, e.default_id, e.bond_name
            ) AS rn
        FROM valid_default_events e
    ) ranked
    WHERE rn = 1
      AND default_date >= DATE('2025-01-01')
      AND default_date < DATE('2026-01-01')
),
company_archive AS (
    SELECT
        COMPANYCODE,
        company_nature
    FROM (
        SELECT
            ia.COMPANYCODE,
            COALESCE(
                CASE ia.COMPANYCVAL
                    WHEN 1 THEN '国家单位'
                    WHEN 4 THEN '中外合资'
                    WHEN 5 THEN '外资独资'
                    WHEN 6 THEN '民营'
                    WHEN 7 THEN '集体企业'
                    WHEN 8 THEN '自然人'
                    WHEN 9 THEN '其他'
                    WHEN 10 THEN '中央国有企业'
                    WHEN 11 THEN '国有企业'
                    WHEN 12 THEN '地方国有企业'
                    WHEN 13 THEN '其他外资企业'
                    WHEN 16 THEN '公众企业'
                END,
                CASE ia.COMPANYNATURE
                    WHEN 150 THEN '有限责任公司'
                    WHEN 159 THEN '其他有限责任公司'
                    WHEN 160 THEN '股份有限公司'
                END
            ) AS company_nature,
            ROW_NUMBER() OVER (
                PARTITION BY ia.COMPANYCODE
                ORDER BY
                    CASE WHEN ia.IFEXISTED = 1 THEN 0 ELSE 1 END,
                    ia.XGRQ DESC,
                    ia.ID DESC
            ) AS rn
        FROM first_subject_default f
        JOIN lc_instiarchive ia
          ON ia.COMPANYCODE = f.companycode
        CROSS JOIN params p
        WHERE (ia.STARTDATE IS NULL OR ia.STARTDATE <= p.as_of_date)
          AND (ia.CLOSEDATE IS NULL OR ia.CLOSEDATE > p.as_of_date)
    ) ranked
    WHERE rn = 1
),
industry_ranked AS (
    SELECT
        bi.COMPANYCODE,
        bi.FIRSTINDUSTRYNAME AS industry_name,
        ROW_NUMBER() OVER (
            PARTITION BY bi.COMPANYCODE
            ORDER BY bi.EFFECTIVEDATE DESC, bi.INFOPUBLDATE DESC, bi.ID DESC
        ) AS rn
    FROM first_subject_default f
    JOIN bond_biindustry bi
      ON bi.COMPANYCODE = f.companycode
    CROSS JOIN params p
    WHERE bi.STANDARD = 22
      AND bi.EFFECTIVEDATE <= p.as_of_date
      AND (bi.CANCELDATE IS NULL OR bi.CANCELDATE > p.as_of_date)
      AND (bi.INFOPUBLDATE IS NULL OR bi.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
)
SELECT
    DATE_FORMAT(f.default_date, '%Y-%m-%d') AS `首次违约日期`,
    f.bond_name AS `首只违约债券简称`,
    f.issuer AS `发行人`,
    ca.company_nature AS `企业性质`,
    ir.industry_name AS `所属行业`,
    CASE f.default_type
        WHEN 1001 THEN '公告预计违约'
        WHEN 1002 THEN '信用评级'
        WHEN 1003 THEN '触发投资者保护条款'
        WHEN 2001 THEN '实质违约'
        WHEN 2002 THEN '技术性违约'
        WHEN 2003 THEN '本息展期'
        WHEN 100301 THEN '触发交叉违约条款'
        ELSE CAST(f.default_type AS CHAR)
    END AS `违约类型`
FROM first_subject_default f
LEFT JOIN company_archive ca
    ON ca.COMPANYCODE = f.companycode
LEFT JOIN industry_ranked ir
    ON ir.COMPANYCODE = f.companycode
   AND ir.rn = 1
ORDER BY f.default_date, f.issuer, f.bond_name;
