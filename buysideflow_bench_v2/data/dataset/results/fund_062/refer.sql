WITH params AS (
    SELECT
        DATE('2025-12-29') AS as_of_date,
        TIMESTAMP('2025-12-30 00:00:00') AS visible_cutoff
),
latest_type AS (
    SELECT
        t.INNERCODE,
        t.SECASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC,
                     t.INSERTTIME DESC,
                     t.ID DESC
        ) AS rn
    FROM mf_jyfundtype t
    JOIN params p
      ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= p.as_of_date
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.as_of_date)
      AND t.INSERTTIME < p.visible_cutoff
),
main_active_equity AS (
    SELECT DISTINCT
        f.INNERCODE,
        f.MAINCODE AS main_code,
        sm.SECUABBR AS fund_name
    FROM mf_fundarchives f
    JOIN secumain sm
      ON sm.INNERCODE = f.INNERCODE
    JOIN latest_type lt
      ON lt.INNERCODE = f.INNERCODE
     AND lt.rn = 1
    JOIN params p
      ON 1 = 1
    WHERE sm.SECUCATEGORY = 8
      AND f.MAINCODE = f.SECURITYCODE
      AND f.MAINCODE IS NOT NULL
      AND f.TYPE IN (2, 3, 4, 7, 8)
      AND f.INVESTMENTTYPE = 16
      AND COALESCE(f.IFFOF, 2) <> 1
      AND lt.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
      AND f.ESTABLISHMENTDATE <= DATE_SUB(p.as_of_date, INTERVAL 3 YEAR)
      AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE > p.as_of_date)
),
latest_scale AS (
    SELECT
        s.INNERCODE,
        s.ENDDATE AS scale_date,
        s.COMBNVI AS combined_scale,
        ROW_NUMBER() OVER (
            PARTITION BY s.INNERCODE
            ORDER BY s.ENDDATE DESC,
                     s.INSERTTIME DESC,
                     s.ID DESC
        ) AS rn
    FROM mf_scaleanalysis s
    JOIN params p
      ON 1 = 1
    WHERE s.ENDDATE <= p.as_of_date
      AND s.COMBNVI IS NOT NULL
      AND s.COMBNVI > 0
      AND s.INSERTTIME < p.visible_cutoff
)
SELECT
    m.main_code AS `主基金代码`,
    m.fund_name AS `基金简称`,
    DATE_FORMAT(ls.scale_date, '%Y-%m-%d') AS `规模截止日期`,
    ROUND(ls.combined_scale / 100000000, 2) AS `合并规模`
FROM main_active_equity m
JOIN latest_scale ls
  ON ls.INNERCODE = m.INNERCODE
 AND ls.rn = 1
ORDER BY ls.combined_scale DESC, m.main_code ASC
LIMIT 5;
