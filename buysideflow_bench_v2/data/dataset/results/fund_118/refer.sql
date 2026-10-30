-- Extracted from fund_118/refer.py (main SQL: sql_query)
-- Template: {{ name }} will be substituted from refer.py before execution.
-- DO NOT change the variable names here without updating refer.py.

WITH params AS (
    SELECT DATE('2026-01-30') AS anchor_date
),
main_fund_base AS (
    SELECT
        fa.INNERCODE,
        sm.SECUCODE AS fund_code,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME) AS fund_name,
        DATE(COALESCE(fa.ESTABLISHMENTDATE, fa.STARTDATE)) AS establishment_date
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN params p
      ON 1 = 1
    WHERE COALESCE(fa.IFFOF, 2) <> 1
      AND fa.TYPE IN (2, 3, 4, 7, 8)
      AND fa.MAINCODE IS NOT NULL
      AND CAST(fa.MAINCODE AS CHAR) <> ''
      AND LPAD(CAST(fa.MAINCODE AS CHAR), 6, '0') = LPAD(CAST(fa.SECURITYCODE AS CHAR), 6, '0')
      AND COALESCE(fa.ESTABLISHMENTDATE, fa.STARTDATE) <= DATE_SUB(p.anchor_date, INTERVAL 5 YEAR)
      AND (fa.EXPIREDATE IS NULL OR DATE(fa.EXPIREDATE) > p.anchor_date)
),
type_asof AS (
    SELECT
        jt.INNERCODE,
        jt.SECASSETCATCODE,
        jt.SECASSETCATNAME,
        jt.THIRDASSETCATCODE,
        jt.THIRDASSETCATNAME,
        ROW_NUMBER() OVER (
            PARTITION BY jt.INNERCODE
            ORDER BY jt.EFFECTIVEDATE DESC, jt.INSERTTIME DESC, jt.ID DESC
        ) AS rn
    FROM mf_jyfundtype jt
    JOIN main_fund_base mb
      ON mb.INNERCODE = jt.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE jt.STANDARD = 75
      AND jt.EFFECTIVEDATE <= p.anchor_date
      AND (jt.CANCELDATE IS NULL OR jt.CANCELDATE > p.anchor_date)
      AND jt.INSERTTIME < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
),
scale_raw AS (
    SELECT
        sa.INNERCODE,
        sa.ENDDATE AS scale_date,
        sa.NVI AS share_scale,
        ROW_NUMBER() OVER (
            PARTITION BY sa.INNERCODE
            ORDER BY sa.ENDDATE DESC, sa.INSERTTIME DESC, sa.ID DESC
        ) AS rn
    FROM mf_scaleanalysis sa
    JOIN type_asof ta
      ON ta.INNERCODE = sa.INNERCODE
     AND ta.rn = 1
    JOIN params p
      ON 1 = 1
    WHERE sa.TYPECODE = 10
      AND sa.ENDDATE <= p.anchor_date
      AND sa.NVI IS NOT NULL
      AND sa.INSERTTIME < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
),
manager_raw AS (
    SELECT
        m.INNERCODE,
        m.PERSONALCODE,
        m.NAME AS manager_name,
        DATE(m.ACCESSIONDATE) AS accession_date,
        ROW_NUMBER() OVER (
            PARTITION BY m.INNERCODE,
                         m.PERSONALCODE,
                         DATE(m.ACCESSIONDATE),
                         COALESCE(DATE(m.DIMISSIONDATE), DATE('9999-12-31'))
            ORDER BY m.ID DESC
        ) AS rn
    FROM mf_fundmanagernew m
    JOIN type_asof ta
      ON ta.INNERCODE = m.INNERCODE
     AND ta.rn = 1
    JOIN params p
      ON 1 = 1
    WHERE m.POSTNAME = 1
      AND m.ACCESSIONDATE IS NOT NULL
      AND DATE(m.ACCESSIONDATE) <= p.anchor_date
      AND (m.DIMISSIONDATE IS NULL OR DATE(m.DIMISSIONDATE) > p.anchor_date)
      AND m.INFOPUBLDATE < DATE_ADD(p.anchor_date, INTERVAL 1 DAY)
),
manager_qualified AS (
    SELECT
        mr.INNERCODE,
        mr.PERSONALCODE,
        mr.manager_name,
        mr.accession_date
    FROM manager_raw mr
    JOIN params p
      ON 1 = 1
    WHERE mr.rn = 1
      AND mr.accession_date <= DATE_SUB(p.anchor_date, INTERVAL 2 YEAR)
),
manager_list AS (
    SELECT
        INNERCODE,
        GROUP_CONCAT(manager_name ORDER BY accession_date, PERSONALCODE SEPARATOR ',') AS qualified_manager_names
    FROM manager_qualified
    GROUP BY INNERCODE
)
SELECT
    mb.INNERCODE,
    mb.fund_code,
    mb.fund_name,
    mb.establishment_date,
    ta.SECASSETCATCODE,
    ta.SECASSETCATNAME,
    ta.THIRDASSETCATCODE,
    ta.THIRDASSETCATNAME,
    COALESCE(ta.THIRDASSETCATNAME, ta.SECASSETCATNAME) AS effective_category_name,
    sr.scale_date,
    ROUND(sr.share_scale / 100000000, 4) AS share_scale_yi,
    ml.qualified_manager_names
FROM main_fund_base mb
JOIN type_asof ta
  ON ta.INNERCODE = mb.INNERCODE
 AND ta.rn = 1
JOIN scale_raw sr
  ON sr.INNERCODE = mb.INNERCODE
 AND sr.rn = 1
JOIN manager_list ml
  ON ml.INNERCODE = mb.INNERCODE
WHERE sr.share_scale >= 200000000
  AND ta.THIRDASSETCATCODE IN ('120201', '120401', '130202')
ORDER BY mb.INNERCODE
