WITH year_ends AS (
    SELECT DATE('2023-12-31') AS year_end
    UNION ALL
    SELECT DATE('2024-12-31') AS year_end
    UNION ALL
    SELECT DATE('2025-12-31') AS year_end
),
type_asof AS (
    SELECT
        y.year_end,
        t.INNERCODE,
        t.FIRSTASSETCATCODE,
        t.SECASSETCATCODE,
        t.SECASSETCATNAME,
        ROW_NUMBER() OVER (
            PARTITION BY y.year_end, t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS rn
    FROM year_ends y
    JOIN mf_jyfundtype t
     ON t.STANDARD = 75
     AND t.EFFECTIVEDATE <= y.year_end
     AND (t.CANCELDATE IS NULL OR t.CANCELDATE > y.year_end)
     AND t.INSERTTIME < DATE_ADD(y.year_end, INTERVAL 1 DAY)
    JOIN mf_fundarchives candidate
      ON candidate.INNERCODE = t.INNERCODE
     AND candidate.IFFOF = 1
     AND candidate.MAINCODE IS NOT NULL
     AND TRIM(CAST(candidate.MAINCODE AS CHAR)) <> ''
     AND LPAD(CAST(candidate.MAINCODE AS CHAR), 6, '0') = candidate.SECURITYCODE
),
fof_main_funds AS (
    SELECT
        y.year_end,
        f.INNERCODE,
        ta.SECASSETCATCODE,
        ta.SECASSETCATNAME
    FROM year_ends y
    JOIN mf_fundarchives f
      ON f.ESTABLISHMENTDATE <= y.year_end
     AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE > y.year_end)
    JOIN type_asof ta
      ON ta.year_end = y.year_end
     AND ta.INNERCODE = f.INNERCODE
     AND ta.rn = 1
    WHERE COALESCE(f.IFFOF, 2) = 1
      AND f.MAINCODE IS NOT NULL
      AND TRIM(CAST(f.MAINCODE AS CHAR)) <> ''
      AND LPAD(CAST(f.MAINCODE AS CHAR), 6, '0') = f.SECURITYCODE
),
scale_asof_ranked AS (
    SELECT
        fm.year_end,
        fm.INNERCODE,
        sa.COMBNVI AS combined_scale,
        ROW_NUMBER() OVER (
            PARTITION BY fm.year_end, fm.INNERCODE
            ORDER BY sa.ENDDATE DESC, sa.INSERTTIME DESC, sa.ID DESC
        ) AS rn
    FROM fof_main_funds fm
    LEFT JOIN mf_scaleanalysis sa
      ON sa.INNERCODE = fm.INNERCODE
     AND sa.ENDDATE <= fm.year_end
     AND sa.INSERTTIME < DATE_ADD(fm.year_end, INTERVAL 1 DAY)
     AND sa.COMBNVI IS NOT NULL
),
scale_asof AS (
    SELECT
        year_end,
        INNERCODE,
        combined_scale
    FROM scale_asof_ranked
    WHERE rn = 1
)
SELECT
    DATE_FORMAT(fm.year_end, '%Y-%m-%d') AS `年末日期`,
    fm.SECASSETCATNAME AS `FOF 二级分类`,
    COUNT(DISTINCT fm.INNERCODE) AS `基金数量`,
    ROUND(SUM(COALESCE(sa.combined_scale, 0)) / 100000000, 2) AS `合并规模（亿元）`
FROM fof_main_funds fm
LEFT JOIN scale_asof sa
  ON sa.year_end = fm.year_end
 AND sa.INNERCODE = fm.INNERCODE
GROUP BY fm.year_end, fm.SECASSETCATCODE, fm.SECASSETCATNAME
ORDER BY fm.year_end, `合并规模（亿元）` DESC, `基金数量` DESC, fm.SECASSETCATCODE;
