WITH anchor_date AS (
    SELECT DATE('2026-02-02') AS anchor_day,
           TIMESTAMP('2026-02-03 00:00:00') AS visible_cutoff
),
dimission_mgr AS (
    SELECT
        fa.INVESTADVISORCODE,
        fm.PERSONALCODE
    FROM mf_fundmanagernew fm
    JOIN mf_fundarchives fa
      ON fa.INNERCODE = fm.INNERCODE
    JOIN anchor_date ad
      ON 1 = 1
    WHERE fm.POSTNAME = 1
      AND fm.DIMISSIONDATE IS NOT NULL
      AND fm.DIMISSIONDATE >= DATE_SUB(ad.anchor_day, INTERVAL 3 YEAR)
      AND fm.DIMISSIONDATE <= ad.anchor_day
      AND fm.INFOPUBLDATE < ad.visible_cutoff
      AND fa.INVESTADVISORCODE IS NOT NULL
    GROUP BY fa.INVESTADVISORCODE, fm.PERSONALCODE
),
incumbent_now AS (
    SELECT
        fm.PERSONALCODE
    FROM mf_fundmanagernew fm
    JOIN anchor_date ad
      ON 1 = 1
    WHERE fm.POSTNAME = 1
      AND (fm.ACCESSIONDATE IS NULL OR fm.ACCESSIONDATE <= ad.anchor_day)
      AND (fm.DIMISSIONDATE IS NULL OR fm.DIMISSIONDATE > ad.anchor_day)
      AND fm.INFOPUBLDATE < ad.visible_cutoff
    GROUP BY fm.PERSONALCODE
),
advisor_info AS (
    SELECT
        ia.INVESTADVISORCODE,
        ia.INVESTADVISORNAME,
        ROW_NUMBER() OVER (
            PARTITION BY ia.INVESTADVISORCODE
            ORDER BY ia.XGRQ DESC, ia.ID DESC
        ) AS rn
    FROM mf_investadvisoroutline ia
),
company_stats AS (
    SELECT
        d.INVESTADVISORCODE,
        COUNT(DISTINCT d.PERSONALCODE) AS fully_dimissioned_manager_cnt
    FROM dimission_mgr d
    LEFT JOIN incumbent_now i
      ON i.PERSONALCODE = d.PERSONALCODE
    WHERE i.PERSONALCODE IS NULL
    GROUP BY d.INVESTADVISORCODE
),
ranked_company AS (
    SELECT
        ai.INVESTADVISORNAME AS advisor_name,
        cs.fully_dimissioned_manager_cnt,
        RANK() OVER (ORDER BY cs.fully_dimissioned_manager_cnt DESC) AS rnk
    FROM company_stats cs
    LEFT JOIN advisor_info ai
      ON ai.INVESTADVISORCODE = cs.INVESTADVISORCODE
     AND ai.rn = 1
)
SELECT
    advisor_name,
    fully_dimissioned_manager_cnt
FROM ranked_company
WHERE rnk <= 10
ORDER BY fully_dimissioned_manager_cnt DESC, advisor_name;
