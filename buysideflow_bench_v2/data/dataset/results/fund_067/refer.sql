WITH params AS (
    SELECT
        DATE('2026-02-03') AS as_of_date,
        DATE('2026-01-01') AS window_start,
        TIMESTAMP('2026-02-04 00:00:00') AS visible_cutoff
),
fund_to_main AS (
    SELECT DISTINCT
        f.INNERCODE,
        f.MAINCODE AS main_fund_code
    FROM mf_fundarchives f
    JOIN secumain sm
      ON sm.INNERCODE = f.INNERCODE
    WHERE sm.SECUCATEGORY = 8
      AND f.MAINCODE IS NOT NULL
      AND f.MAINCODE <> ''
),
departure_events AS (
    SELECT DISTINCT
        ftm.main_fund_code,
        fm.PERSONALCODE,
        DATE(fm.DIMISSIONDATE) AS dimission_date
    FROM mf_fundmanagernew fm
    JOIN fund_to_main ftm
      ON ftm.INNERCODE = fm.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE fm.POSTNAME = 1
      AND fm.DIMISSIONDATE IS NOT NULL
      AND fm.DIMISSIONDATE >= p.window_start
      AND fm.DIMISSIONDATE < p.visible_cutoff
      AND fm.INFOPUBLDATE < p.visible_cutoff
),
event_counts AS (
    SELECT
        main_fund_code,
        COUNT(*) AS departure_count
    FROM departure_events
    GROUP BY main_fund_code
),
main_fund_info AS (
    SELECT
        x.main_fund_code,
        x.fund_name
    FROM (
        SELECT
            f.MAINCODE AS main_fund_code,
            sm.SECUABBR AS fund_name,
            ROW_NUMBER() OVER (
                PARTITION BY f.MAINCODE
                ORDER BY
                    CASE WHEN f.MAINCODE = f.SECURITYCODE THEN 0 ELSE 1 END,
                    f.ESTABLISHMENTDATE DESC,
                    f.INNERCODE DESC
            ) AS rn
        FROM mf_fundarchives f
        JOIN secumain sm
          ON sm.INNERCODE = f.INNERCODE
        WHERE sm.SECUCATEGORY = 8
          AND f.MAINCODE IS NOT NULL
          AND f.MAINCODE <> ''
    ) x
    WHERE x.rn = 1
)
SELECT
    ec.main_fund_code AS `主基金代码`,
    mfi.fund_name AS `基金简称`,
    ec.departure_count AS `基金经理离任人次`
FROM event_counts ec
JOIN main_fund_info mfi
  ON mfi.main_fund_code = ec.main_fund_code
ORDER BY ec.departure_count DESC, ec.main_fund_code ASC
LIMIT 10;
