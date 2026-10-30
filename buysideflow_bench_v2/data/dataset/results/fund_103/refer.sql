WITH anchor_date AS (
    SELECT DATE('2026-02-27') AS anchor_day
),
type_asof AS (
    SELECT
        t.INNERCODE,
        t.SECASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS rn
    FROM mf_jyfundtype t
    JOIN anchor_date ad
      ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= ad.anchor_day
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > ad.anchor_day)
      AND t.INSERTTIME < DATE_ADD(ad.anchor_day, INTERVAL 1 DAY)
),
money_share AS (
    SELECT
        fa.INNERCODE,
        fa.MAINCODE AS main_code,
        fa.SECURITYCODE AS share_code,
        sm.SECUABBR AS share_name
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
    JOIN type_asof ta
      ON ta.INNERCODE = fa.INNERCODE
     AND ta.rn = 1
    JOIN anchor_date ad
      ON 1 = 1
    WHERE sm.SECUCATEGORY = 8
      AND ta.SECASSETCATCODE = 1402
      AND fa.TYPE IN (2, 3, 4, 7, 8)
      AND fa.ESTABLISHMENTDATE <= ad.anchor_day
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > ad.anchor_day)
      AND fa.MAINCODE = fa.SECURITYCODE
),
latest_scale AS (
    SELECT
        s.INNERCODE,
        s.ENDDATE AS scale_date,
        s.COMBNVI AS combined_scale,
        COALESCE(s.NVII, s.TOTALASSET, s.COMBNVII, s.COMBNVI) AS share_scale,
        ROW_NUMBER() OVER (
            PARTITION BY s.INNERCODE
            ORDER BY s.ENDDATE DESC, s.INSERTTIME DESC, s.ID DESC
        ) AS rn
    FROM mf_scaleanalysis s
    JOIN anchor_date ad
      ON 1 = 1
    WHERE s.ENDDATE <= ad.anchor_day
      AND s.COMBNVI IS NOT NULL
      AND s.INSERTTIME < DATE_ADD(ad.anchor_day, INTERVAL 1 DAY)
),
share_snapshot AS (
    SELECT
        ms.main_code,
        ms.share_code,
        ms.share_name,
        ls.scale_date,
        ls.combined_scale,
        ls.share_scale,
        ROW_NUMBER() OVER (
            PARTITION BY ms.main_code
            ORDER BY
                ls.scale_date DESC,
                CASE WHEN ms.share_code = ms.main_code THEN 0 ELSE 1 END,
                COALESCE(ls.share_scale, 0) DESC,
                ms.share_code ASC
        ) AS rep_rn
    FROM money_share ms
    JOIN latest_scale ls
      ON ls.INNERCODE = ms.INNERCODE
     AND ls.rn = 1
)
SELECT
    share_code AS `主份额代码`,
    share_name AS `基金简称`,
    DATE_FORMAT(scale_date, '%Y-%m-%d') AS `规模截止日期`,
    ROUND(combined_scale / 100000000, 2) AS `合并规模（亿元）`
FROM share_snapshot
WHERE rep_rn = 1
ORDER BY combined_scale DESC, share_code ASC
LIMIT 5;
