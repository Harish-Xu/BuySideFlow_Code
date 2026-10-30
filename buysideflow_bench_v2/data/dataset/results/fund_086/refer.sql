WITH anchor_date AS (
    SELECT
        DATE('2025-12-22') AS anchor_day,
        TIMESTAMP('2025-12-23 00:00:00') AS visible_cutoff
),
latest_type AS (
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
      AND t.INSERTTIME < ad.visible_cutoff
      AND t.UPDATETIME < ad.visible_cutoff
),
fund_pool AS (
    SELECT
        fa.INNERCODE,
        sm.SECUCODE,
        sm.SECUABBR
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
    JOIN latest_type lt
      ON lt.INNERCODE = fa.INNERCODE
     AND lt.rn = 1
    JOIN anchor_date ad
      ON 1 = 1
    WHERE sm.SECUCATEGORY = 8
      AND lt.SECASSETCATCODE = 1301
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= ad.anchor_day
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > ad.anchor_day)
      AND COALESCE(fa.IFFOF, 2) <> 1
),
latest_pr AS (
    SELECT
        p.INNERCODE,
        p.ENDDATE,
        p.APPLYINGTYPE,
        p.LARGEAPPLYINGMAX,
        ROW_NUMBER() OVER (
            PARTITION BY p.INNERCODE
            ORDER BY p.ENDDATE DESC, p.INSERTTIME DESC, p.ID DESC
        ) AS rn
    FROM mf_purchaseandredeem p
    JOIN fund_pool fp
      ON fp.INNERCODE = p.INNERCODE
    JOIN anchor_date ad
      ON 1 = 1
    WHERE p.ENDDATE <= ad.anchor_day
      AND p.INSERTTIME < ad.visible_cutoff
      AND p.UPDATETIME < ad.visible_cutoff
),
latest_scale AS (
    SELECT
        s.INNERCODE,
        s.ENDDATE,
        s.COMBNVI,
        ROW_NUMBER() OVER (
            PARTITION BY s.INNERCODE
            ORDER BY s.ENDDATE DESC, s.INSERTTIME DESC, s.ID DESC
        ) AS rn
    FROM mf_scaleanalysis s
    JOIN fund_pool fp
      ON fp.INNERCODE = s.INNERCODE
    JOIN anchor_date ad
      ON 1 = 1
    WHERE s.ENDDATE <= ad.anchor_day
      AND s.INSERTTIME < ad.visible_cutoff
      AND s.UPDATETIME < ad.visible_cutoff
      AND s.COMBNVI IS NOT NULL
      AND COALESCE(s.FUNDREMARK, 1) = 1
),
scale_1y AS (
    SELECT
        s.INNERCODE,
        s.ENDDATE,
        s.COMBNVI,
        ROW_NUMBER() OVER (
            PARTITION BY s.INNERCODE
            ORDER BY s.INSERTTIME DESC, s.ID DESC
        ) AS rn
    FROM mf_scaleanalysis s
    JOIN latest_scale ls
      ON ls.INNERCODE = s.INNERCODE
     AND ls.rn = 1
    JOIN anchor_date ad
      ON 1 = 1
    WHERE s.ENDDATE = DATE_SUB(ls.ENDDATE, INTERVAL 1 YEAR)
      AND s.ENDDATE <= ad.anchor_day
      AND s.INSERTTIME < ad.visible_cutoff
      AND s.UPDATETIME < ad.visible_cutoff
      AND s.COMBNVI IS NOT NULL
      AND COALESCE(s.FUNDREMARK, 1) = 1
)
SELECT
    fp.SECUCODE AS `基金代码`,
    fp.SECUABBR AS `基金简称`,
    pr.APPLYINGTYPE AS `申购状态`,
    ROUND(pr.LARGEAPPLYINGMAX / 10000, 2) AS `大额申购上限(万元)`,
    ROUND(ls.COMBNVI / 10000, 2) AS `最新合并规模(万元)`,
    ROUND(s1.COMBNVI / 10000, 2) AS `去年同期合并规模(万元)`,
    ROUND((ls.COMBNVI / NULLIF(s1.COMBNVI, 0) - 1) * 100, 2) AS `规模同比增幅`
FROM latest_pr pr
JOIN latest_scale ls
    ON ls.INNERCODE = pr.INNERCODE
   AND ls.rn = 1
JOIN scale_1y s1
    ON s1.INNERCODE = pr.INNERCODE
   AND s1.rn = 1
JOIN fund_pool fp
    ON fp.INNERCODE = pr.INNERCODE
WHERE pr.rn = 1
  AND pr.APPLYINGTYPE = '限大额'
  AND pr.LARGEAPPLYINGMAX IS NOT NULL
  AND pr.LARGEAPPLYINGMAX > 0
  AND s1.COMBNVI >= 100000000
  AND s1.COMBNVI > 0
  AND ls.COMBNVI > s1.COMBNVI
ORDER BY `规模同比增幅` DESC, fp.SECUCODE ASC
LIMIT 50;
