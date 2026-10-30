WITH params AS (
    SELECT
        DATE('2025-11-30') AS anchor_day,
        TIMESTAMP('2025-12-01 00:00:00') AS visible_cutoff
),
fund_pool AS (
    SELECT
        f.INNERCODE,
        f.SECURITYCODE AS fund_code,
        sm.SECUABBR AS fund_name
    FROM mf_fundarchives f
    JOIN secumain sm
      ON sm.INNERCODE = f.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN params p
      ON 1 = 1
    WHERE f.MAINCODE = f.SECURITYCODE
      AND f.TYPE = 2
      AND f.ESTABLISHMENTDATE <= p.anchor_day
      AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE >= p.anchor_day)
),
latest_status AS (
    SELECT
        pr.INNERCODE,
        pr.ENDDATE,
        pr.APPLYINGTYPE,
        pr.LARGEAPPLYINGMAX,
        ROW_NUMBER() OVER (
            PARTITION BY pr.INNERCODE
            ORDER BY
                pr.ENDDATE DESC,
                pr.INSERTTIME DESC,
                pr.UPDATETIME DESC,
                pr.ID DESC
        ) AS rn
    FROM mf_purchaseandredeem pr
    JOIN fund_pool fp
      ON fp.INNERCODE = pr.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE pr.ENDDATE <= p.anchor_day
      AND pr.INSERTTIME < p.visible_cutoff
      AND pr.UPDATETIME < p.visible_cutoff
)
SELECT
    fp.fund_code,
    fp.fund_name,
    DATE(ls.ENDDATE) AS status_end_date,
    ls.LARGEAPPLYINGMAX AS large_applying_max
FROM latest_status ls
JOIN fund_pool fp
  ON fp.INNERCODE = ls.INNERCODE
WHERE ls.rn = 1
  AND ls.APPLYINGTYPE = '限大额'
  AND ls.LARGEAPPLYINGMAX > 0
ORDER BY ls.LARGEAPPLYINGMAX ASC, fp.fund_code ASC
LIMIT 20;
