WITH target_fund AS (
    SELECT fa.INNERCODE, sm.SECUCODE, sm.SECUABBR AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain sm ON sm.INNERCODE = fa.INNERCODE
    WHERE sm.SECUCODE = '001300'
      AND fa.ESTABLISHMENTDATE IS NOT NULL
),
-- 观察时点仍在任的基金经理
target_manager AS (
    SELECT DISTINCT fm.PERSONALCODE
    FROM mf_fundmanagernew fm
    JOIN target_fund tf ON tf.INNERCODE = fm.INNERCODE
    WHERE fm.ACCESSIONDATE <= '2026-02-09'
      AND (fm.DIMISSIONDATE IS NULL OR fm.DIMISSIONDATE > '2026-02-09')
      AND fm.INFOPUBLDATE < '2026-02-10'
),
-- 在管主基金：任职截至观察时点 且 主基金判断（母基金 = SECURITYCODE 自引用或 MAINCODE 为空）
current_main_fund AS (
    SELECT fm.PERSONALCODE, fm.INNERCODE, fm.ACCESSIONDATE
    FROM mf_fundmanagernew fm
    JOIN mf_fundarchives fa ON fa.INNERCODE = fm.INNERCODE
    WHERE fm.ACCESSIONDATE <= '2026-02-09'
      AND (fm.DIMISSIONDATE IS NULL OR fm.DIMISSIONDATE > '2026-02-09')
      AND fm.INFOPUBLDATE < '2026-02-10'
      AND (fa.MAINCODE IS NULL OR fa.MAINCODE = fa.SECURITYCODE)
),
-- 规模：取观察时点之前最近一期披露（ENDDATE <= 2026-02-09 → 实际取到 2025-12-31）
latest_scale AS (
    SELECT x.INNERCODE, x.ENDDATE AS scale_date, x.COMBNVI AS latest_aum
    FROM (
        SELECT
            sa.INNERCODE,
            sa.ENDDATE,
            sa.COMBNVI,
            ROW_NUMBER() OVER (
                PARTITION BY sa.INNERCODE
                ORDER BY sa.ENDDATE DESC, sa.INSERTTIME DESC, sa.ID DESC
            ) AS rn
        FROM mf_scaleanalysis sa
        WHERE sa.COMBNVI IS NOT NULL
          AND sa.ENDDATE <= '2026-02-09'
          AND sa.INSERTTIME < '2026-02-10'
    ) x
    WHERE x.rn = 1
),
manager_stat AS (
    SELECT
        cmf.PERSONALCODE,
        COUNT(DISTINCT cmf.INNERCODE) AS current_main_fund_count,
        SUM(COALESCE(ls.latest_aum, 0)) / 100000000 AS total_aum_100m,
        MIN(cmf.ACCESSIONDATE) AS earliest_accession_date
    FROM current_main_fund cmf
    JOIN target_manager tm ON tm.PERSONALCODE = cmf.PERSONALCODE
    LEFT JOIN latest_scale ls ON ls.INNERCODE = cmf.INNERCODE
    GROUP BY cmf.PERSONALCODE
)
SELECT
    tf.SECUCODE AS fund_code,
    tf.fund_name,
    pi.PERSONALCODE AS manager_id,
    pi.CHINESENAME AS manager_name,
    ms.current_main_fund_count,
    ROUND(ms.total_aum_100m, 2) AS total_aum_100m,
    ms.earliest_accession_date
FROM manager_stat ms
JOIN mf_personalinfo pi ON pi.PERSONALCODE = ms.PERSONALCODE
CROSS JOIN target_fund tf
ORDER BY ms.total_aum_100m DESC, ms.earliest_accession_date;
