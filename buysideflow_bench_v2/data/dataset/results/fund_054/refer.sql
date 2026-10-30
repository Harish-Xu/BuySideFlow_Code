WITH params AS (
    SELECT DATE('2026-01-22') AS as_of_date,
           DATE_ADD(DATE('2026-01-22'), INTERVAL 1 DAY) AS visible_cutoff,
           DATE_SUB(DATE('2026-01-22'), INTERVAL 12 MONTH) AS fresh_start_date,
           300000000 AS level1_abs_drop,
           0.70 AS level1_drop_ratio,
           0.05 AS level1_holdratio_cap,
           200000000 AS level2_abs_drop,
           0.50 AS level2_drop_ratio,
           0.10 AS level2_holdratio_cap
),
fund_type_asof AS (
    SELECT x.INNERCODE,
           x.SECASSETCATCODE
    FROM (
        SELECT jt.INNERCODE,
               jt.SECASSETCATCODE,
               ROW_NUMBER() OVER (
                   PARTITION BY jt.INNERCODE
                   ORDER BY jt.EFFECTIVEDATE DESC,
                            jt.INSERTTIME DESC,
                            jt.ID DESC
               ) AS rn
        FROM mf_jyfundtype jt
        JOIN params p
          ON jt.STANDARD = 75
         AND jt.EFFECTIVEDATE <= p.as_of_date
         AND (jt.CANCELDATE IS NULL OR jt.CANCELDATE > p.as_of_date)
         AND jt.INSERTTIME < p.visible_cutoff
         AND jt.UPDATETIME < p.visible_cutoff
    ) x
    WHERE x.rn = 1
      AND x.SECASSETCATCODE IN (1301, 1302, 1303, 1305)
),
qualified_funds AS (
    SELECT fa.INNERCODE,
           fa.SECURITYCODE,
           fa.MAINCODE
    FROM mf_fundarchives fa
    JOIN fund_type_asof ft
      ON ft.INNERCODE = fa.INNERCODE
    JOIN params p
      ON fa.TYPE = 2
     AND fa.FLOATTYPE = 2
     AND COALESCE(fa.IFFOF, 2) <> 1
     AND fa.MAINCODE = fa.SECURITYCODE
     AND (fa.ESTABLISHMENTDATE IS NULL OR fa.ESTABLISHMENTDATE <= p.as_of_date)
     AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > p.as_of_date)
     AND (fa.INSERTTIME IS NULL OR fa.INSERTTIME < p.visible_cutoff)
),
latest_report AS (
    SELECT x.INNERCODE,
           x.ENDDATE AS latest_enddate,
           x.INFOPUBLDATE AS latest_infopubdate
    FROM (
        SELECT sh.INNERCODE,
               sh.ENDDATE,
               sh.INFOPUBLDATE,
               sh.INSERTTIME,
               sh.UPDATETIME,
               sh.ID,
               ROW_NUMBER() OVER (
                   PARTITION BY sh.INNERCODE
                   ORDER BY sh.ENDDATE DESC,
                            sh.INFOPUBLDATE DESC,
                            sh.INSERTTIME DESC,
                            sh.ID DESC
               ) AS rn
        FROM mf_shinfluence sh
        JOIN qualified_funds qf
          ON qf.INNERCODE = sh.INNERCODE
        JOIN params p
          ON sh.INFOPUBLDATE <= p.as_of_date
         AND sh.INSERTTIME < p.visible_cutoff
         AND sh.UPDATETIME < p.visible_cutoff
    ) x
    JOIN params p
      ON x.INFOPUBLDATE >= p.fresh_start_date
    WHERE x.rn = 1
),
holder_rows AS (
    SELECT y.INNERCODE,
           y.ENDDATE,
           y.INFOPUBLDATE,
           y.SERIALNUMBER,
           y.INVESTORID,
           y.STARTSHARES,
           y.HOLDINGVOLUME,
           CASE
               WHEN y.HOLDRATIO > 1 THEN y.HOLDRATIO / 100.0
               ELSE y.HOLDRATIO
           END AS holdratio_norm
    FROM (
        SELECT sh.INNERCODE,
               sh.ENDDATE,
               sh.INFOPUBLDATE,
               sh.SERIALNUMBER,
               sh.INVESTORID,
               sh.STARTSHARES,
               sh.HOLDINGVOLUME,
               sh.HOLDRATIO,
               ROW_NUMBER() OVER (
                   PARTITION BY sh.INNERCODE,
                                sh.ENDDATE,
                                sh.INFOPUBLDATE,
                                sh.SERIALNUMBER
                   ORDER BY sh.UPDATETIME DESC,
                            sh.INSERTTIME DESC,
                            sh.ID DESC
               ) AS rn
        FROM mf_shinfluence sh
        JOIN latest_report lr
          ON lr.INNERCODE = sh.INNERCODE
         AND lr.latest_enddate = sh.ENDDATE
         AND lr.latest_infopubdate = sh.INFOPUBLDATE
        JOIN params p
          ON sh.INSERTTIME < p.visible_cutoff
         AND sh.UPDATETIME < p.visible_cutoff
        WHERE sh.HOLDERNATURE = 2
          AND sh.SERIALNUMBER IN ('机构1', '机构2')
    ) y
    WHERE y.rn = 1
      AND y.STARTSHARES IS NOT NULL
      AND y.STARTSHARES > 0
      AND y.HOLDINGVOLUME IS NOT NULL
      AND y.HOLDRATIO IS NOT NULL
),
holder_metrics AS (
    SELECT hr.INNERCODE,
           hr.ENDDATE,
           hr.INFOPUBLDATE,
           hr.SERIALNUMBER,
           hr.INVESTORID,
           hr.STARTSHARES,
           hr.HOLDINGVOLUME,
           hr.holdratio_norm,
           (hr.STARTSHARES - hr.HOLDINGVOLUME) AS drop_shares,
           (hr.STARTSHARES - hr.HOLDINGVOLUME) / NULLIF(hr.STARTSHARES, 0) AS drop_ratio
    FROM holder_rows hr
),
warning_candidates AS (
    SELECT '一级预警' AS alert_level,
           1 AS severity,
           hm.*
    FROM holder_metrics hm
    JOIN params p
      ON hm.drop_shares >= p.level1_abs_drop
     AND hm.drop_ratio >= p.level1_drop_ratio
     AND hm.holdratio_norm <= p.level1_holdratio_cap
    UNION ALL
    SELECT '二级预警' AS alert_level,
           2 AS severity,
           hm.*
    FROM holder_metrics hm
    JOIN params p
      ON hm.drop_shares >= p.level2_abs_drop
     AND hm.drop_ratio >= p.level2_drop_ratio
     AND hm.holdratio_norm <= p.level2_holdratio_cap
    WHERE NOT (
        hm.drop_shares >= p.level1_abs_drop
        AND hm.drop_ratio >= p.level1_drop_ratio
        AND hm.holdratio_norm <= p.level1_holdratio_cap
    )
),
fund_pick AS (
    SELECT wc.*,
           sm.SECUCODE,
           sm.CHINAMEABBR,
           ROW_NUMBER() OVER (
               PARTITION BY wc.INNERCODE
               ORDER BY wc.severity ASC,
                        wc.drop_shares DESC,
                        wc.SERIALNUMBER ASC
           ) AS rn_fund
    FROM warning_candidates wc
    JOIN secumain sm
      ON sm.INNERCODE = wc.INNERCODE
     AND sm.SECUCATEGORY = 8
)
SELECT fp.alert_level AS `预警层级`,
       fp.SECUCODE AS `基金代码`,
       fp.CHINAMEABBR AS `基金简称`,
       DATE(fp.INFOPUBLDATE) AS `披露日期`,
       CASE
           WHEN fp.INVESTORID IS NULL THEN fp.SERIALNUMBER
           ELSE CONCAT(fp.SERIALNUMBER, ':', CAST(fp.INVESTORID AS CHAR))
       END AS `核心机构标识`,
       ROUND(fp.STARTSHARES / 100000000.0, 2) AS `期初持有份额(亿份)`,
       ROUND(fp.HOLDINGVOLUME / 100000000.0, 2) AS `期末持有份额(亿份)`,
       ROUND(fp.drop_shares / 100000000.0, 2) AS `份额下降额(亿份)`,
       ROUND(fp.drop_ratio * 100, 2) AS `下降比例(%)`,
       ROUND(fp.holdratio_norm * 100, 2) AS `期末持有占比(%)`
FROM fund_pick fp
WHERE fp.rn_fund = 1
ORDER BY fp.severity ASC,
         fp.drop_shares DESC,
         fp.SECUCODE ASC;
