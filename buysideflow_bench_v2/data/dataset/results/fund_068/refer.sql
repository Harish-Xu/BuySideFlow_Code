WITH params AS (
    SELECT
        DATE('2025-12-31') AS as_of_date,
        TIMESTAMP('2026-01-01 00:00:00') AS visible_cutoff
),
fund_company AS (
    SELECT DISTINCT
        f.INNERCODE,
        f.INVESTADVISORCODE
    FROM mf_fundarchives f
    JOIN secumain sm
      ON sm.INNERCODE = f.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE sm.SECUCATEGORY = 8
      AND f.INVESTADVISORCODE IS NOT NULL
      AND (f.ESTABLISHMENTDATE IS NULL OR f.ESTABLISHMENTDATE <= p.as_of_date)
      AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE > p.as_of_date)
      AND (f.INSERTTIME IS NULL OR f.INSERTTIME < p.visible_cutoff)
),
visible_manager_records AS (
    SELECT
        fm.INNERCODE,
        fm.PERSONALCODE,
        fm.ACCESSIONDATE,
        fm.DIMISSIONDATE
    FROM mf_fundmanagernew fm
    JOIN params p
      ON 1 = 1
    WHERE fm.POSTNAME = 1
      AND fm.PERSONALCODE IS NOT NULL
      AND fm.ACCESSIONDATE IS NOT NULL
      AND fm.ACCESSIONDATE <= p.as_of_date
      AND (fm.INFOPUBLDATE IS NULL OR fm.INFOPUBLDATE < p.visible_cutoff)
),
current_manager_company AS (
    SELECT DISTINCT
        fc.INVESTADVISORCODE,
        vm.PERSONALCODE
    FROM visible_manager_records vm
    JOIN fund_company fc
      ON fc.INNERCODE = vm.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE vm.DIMISSIONDATE IS NULL
       OR vm.DIMISSIONDATE > p.as_of_date
),
manager_first_accession AS (
    SELECT
        PERSONALCODE,
        MIN(DATE(ACCESSIONDATE)) AS first_accession_date
    FROM visible_manager_records
    GROUP BY PERSONALCODE
),
company_stats AS (
    SELECT
        cmc.INVESTADVISORCODE,
        COUNT(*) AS incumbent_manager_count,
        ROUND(AVG(DATEDIFF(p.as_of_date, mfa.first_accession_date) / 365.0), 1) AS avg_management_years
    FROM current_manager_company cmc
    JOIN manager_first_accession mfa
      ON mfa.PERSONALCODE = cmc.PERSONALCODE
    JOIN params p
      ON 1 = 1
    GROUP BY cmc.INVESTADVISORCODE
),
advisor_info AS (
    SELECT
        x.INVESTADVISORCODE,
        x.INVESTADVISORNAME
    FROM (
        SELECT
            ia.INVESTADVISORCODE,
            ia.INVESTADVISORNAME,
            ROW_NUMBER() OVER (
                PARTITION BY ia.INVESTADVISORCODE
                ORDER BY ia.XGRQ DESC, ia.ID DESC
            ) AS rn
        FROM mf_investadvisoroutline ia
    ) x
    WHERE x.rn = 1
)
SELECT
    ai.INVESTADVISORNAME AS `基金公司名称`,
    cs.incumbent_manager_count AS `在任基金经理人数`,
    cs.avg_management_years AS `平均管理年限`
FROM company_stats cs
JOIN advisor_info ai
  ON ai.INVESTADVISORCODE = cs.INVESTADVISORCODE
ORDER BY cs.incumbent_manager_count DESC,
         cs.avg_management_years DESC,
         ai.INVESTADVISORNAME ASC
LIMIT 10;
