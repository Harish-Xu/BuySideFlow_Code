WITH anchor_date AS (
    SELECT DATE('2025-12-31') AS anchor_day
),
type_asof AS (
    SELECT
        t.INNERCODE,
        t.SECASSETCATCODE,
        t.SECASSETCATNAME,
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
share_pool AS (
    SELECT
        f.INNERCODE,
        f.SECURITYCODE,
        COALESCE(NULLIF(f.MAINCODE, ''), f.SECURITYCODE) AS main_fund_code,
        DATE(f.ESTABLISHMENTDATE) AS establishment_date,
        sm.SECUCODE,
        sm.SECUABBR,
        sm.CHINAMEABBR,
        ta.SECASSETCATNAME
    FROM mf_fundarchives f
    JOIN secumain sm
      ON sm.INNERCODE = f.INNERCODE
    JOIN type_asof ta
      ON ta.INNERCODE = f.INNERCODE
     AND ta.rn = 1
    JOIN anchor_date ad
      ON 1 = 1
    WHERE COALESCE(f.IFFOF, 2) = 1
      AND COALESCE(f.IFPENSIONTARGET, 2) = 1
      AND ta.SECASSETCATCODE = 1804
      AND f.ESTABLISHMENTDATE <= ad.anchor_day
      AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE > ad.anchor_day)
),
y_share_pool AS (
    SELECT
        sp.INNERCODE,
        sp.main_fund_code,
        sp.SECUCODE AS fund_code,
        sp.SECUABBR AS fund_name,
        sp.establishment_date,
        sp.SECASSETCATNAME
    FROM share_pool sp
    WHERE COALESCE(sp.SECUABBR, '') LIKE '%Y'
       OR COALESCE(sp.CHINAMEABBR, '') LIKE '%Y'
),
scale_asof_ranked AS (
    SELECT
        yp.INNERCODE,
        sa.COMBNVI AS combined_scale,
        ROW_NUMBER() OVER (
            PARTITION BY yp.INNERCODE
            ORDER BY sa.ENDDATE DESC, sa.INSERTTIME DESC, sa.ID DESC
        ) AS rn
    FROM y_share_pool yp
    JOIN mf_scaleanalysis sa
      ON sa.INNERCODE = yp.INNERCODE
    JOIN anchor_date ad
      ON 1 = 1
    WHERE sa.ENDDATE <= ad.anchor_day
      AND sa.INSERTTIME < DATE_ADD(ad.anchor_day, INTERVAL 1 DAY)
),
scale_asof AS (
    SELECT
        INNERCODE,
        combined_scale
    FROM scale_asof_ranked
    WHERE rn = 1
),
manager_asof AS (
    SELECT
        sp.main_fund_code,
        COUNT(DISTINCT fm.PERSONALCODE) AS incumbent_manager_count
    FROM mf_fundmanagernew fm
    JOIN share_pool sp
      ON sp.INNERCODE = fm.INNERCODE
    JOIN anchor_date ad
      ON 1 = 1
    WHERE (fm.ACCESSIONDATE IS NULL OR fm.ACCESSIONDATE <= ad.anchor_day)
      AND (fm.DIMISSIONDATE IS NULL OR fm.DIMISSIONDATE > ad.anchor_day)
      AND fm.POSTNAME = 1
      AND fm.INFOPUBLDATE < DATE_ADD(ad.anchor_day, INTERVAL 1 DAY)
    GROUP BY sp.main_fund_code
)
SELECT
    yp.fund_code,
    yp.fund_name,
    yp.establishment_date,
    yp.SECASSETCATNAME AS fof_second_type,
    ROUND(sa.combined_scale / 100000000, 2) AS total_combined_scale_yi,
    COALESCE(ma.incumbent_manager_count, 0) AS incumbent_manager_count
FROM y_share_pool yp
LEFT JOIN scale_asof sa
  ON sa.INNERCODE = yp.INNERCODE
LEFT JOIN manager_asof ma
  ON ma.main_fund_code = yp.main_fund_code
ORDER BY sa.combined_scale IS NULL ASC, sa.combined_scale DESC, yp.fund_code
LIMIT 10;
