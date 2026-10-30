WITH params AS (
    SELECT
        DATE('2026-01-05') AS as_of_date,
        DATE('2025-12-25') AS event_start,
        DATE('2025-12-31') AS event_end,
        DATE('2026-01-06') AS visible_cutoff
),
main_funds AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS main_fund_code,
        sm.SECUABBR AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain sm
        ON sm.INNERCODE = fa.INNERCODE
       AND sm.SECUCATEGORY = 8
    JOIN params p ON 1 = 1
    WHERE fa.MAINCODE = fa.SECURITYCODE
      AND fa.TYPE IN (2, 3, 4, 8)
      AND fa.ESTABLISHMENTDATE <= p.as_of_date
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.as_of_date)
      AND (fa.INSERTTIME IS NULL OR fa.INSERTTIME < p.visible_cutoff)
),
departure_events AS (
    SELECT *
    FROM (
        SELECT
            mf.INNERCODE,
            mf.main_fund_code,
            mf.fund_name,
            fm.PERSONALCODE,
            fm.NAME AS departed_manager,
            DATE(fm.ACCESSIONDATE) AS accession_date,
            DATE(fm.DIMISSIONDATE) AS dimission_date,
            ROW_NUMBER() OVER (
                PARTITION BY mf.INNERCODE, fm.PERSONALCODE, DATE(fm.ACCESSIONDATE), DATE(fm.DIMISSIONDATE)
                ORDER BY fm.INFOPUBLDATE DESC, fm.JSID DESC
            ) AS rn
        FROM main_funds mf
        JOIN mf_fundmanagernew fm
            ON fm.INNERCODE = mf.INNERCODE
        JOIN params p ON 1 = 1
        WHERE fm.POSTNAME = 1
          AND fm.DIMISSIONDATE IS NOT NULL
          AND fm.DIMISSIONDATE >= p.event_start
          AND fm.DIMISSIONDATE < DATE_ADD(p.event_end, INTERVAL 1 DAY)
          AND fm.INFOPUBLDATE < p.visible_cutoff
    ) x
    WHERE rn = 1
),
current_manager_latest AS (
    SELECT *
    FROM (
        SELECT
            mf.INNERCODE,
            fm.PERSONALCODE,
            fm.NAME,
            fm.ACCESSIONDATE,
            ROW_NUMBER() OVER (
                PARTITION BY mf.INNERCODE, fm.PERSONALCODE
                ORDER BY fm.ACCESSIONDATE DESC, fm.INFOPUBLDATE DESC, fm.JSID DESC
            ) AS rn
        FROM main_funds mf
        JOIN mf_fundmanagernew fm
            ON fm.INNERCODE = mf.INNERCODE
        JOIN params p ON 1 = 1
        WHERE fm.POSTNAME = 1
          AND fm.ACCESSIONDATE <= p.as_of_date
          AND (fm.DIMISSIONDATE IS NULL OR fm.DIMISSIONDATE > p.as_of_date)
          AND fm.INFOPUBLDATE < p.visible_cutoff
    ) x
    WHERE rn = 1
),
current_manager_names AS (
    SELECT
        INNERCODE,
        GROUP_CONCAT(NAME ORDER BY ACCESSIONDATE, PERSONALCODE SEPARATOR ',') AS incumbent_managers
    FROM current_manager_latest
    GROUP BY INNERCODE
)
SELECT
    de.main_fund_code AS `主基金代码`,
    de.fund_name AS `基金简称`,
    de.departed_manager AS `离任基金经理`,
    de.accession_date AS `任职开始日`,
    de.dimission_date AS `离任日期`,
    cmn.incumbent_managers AS `截至2026-01-05仍在任基金经理名单`
FROM departure_events de
LEFT JOIN current_manager_names cmn
    ON cmn.INNERCODE = de.INNERCODE
ORDER BY de.dimission_date ASC, de.main_fund_code ASC, de.departed_manager ASC;
