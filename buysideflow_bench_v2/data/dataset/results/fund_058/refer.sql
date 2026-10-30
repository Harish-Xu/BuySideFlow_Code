WITH params AS (
    SELECT DATE('2025-03-28') AS observation_date,
           TIMESTAMP('2025-03-29 00:00:00') AS visible_cutoff
),
current_mgr AS (
    SELECT
        m.INNERCODE,
        m.PERSONALCODE,
        m.NAME,
        ROW_NUMBER() OVER (
            PARTITION BY m.INNERCODE, m.PERSONALCODE
            ORDER BY COALESCE(m.INFOPUBLDATE, '1900-01-01') DESC, m.ID DESC
        ) AS rn
    FROM mf_fundmanagernew m
    CROSS JOIN params p
    WHERE m.POSTNAME = 1
      AND m.PERSONALCODE IS NOT NULL
      AND m.ACCESSIONDATE <= p.observation_date
      AND (m.DIMISSIONDATE IS NULL OR m.DIMISSIONDATE > p.observation_date)
      AND m.INFOPUBLDATE < p.visible_cutoff
),
person_latest AS (
    SELECT
        p.PERSONALCODE,
        p.CHINESENAME,
        ROW_NUMBER() OVER (
            PARTITION BY p.PERSONALCODE
            ORDER BY p.INSERTTIME DESC, p.ID DESC
        ) AS rn
    FROM mf_personalinfo p
),
advisor_latest AS (
    SELECT
        a.INVESTADVISORCODE,
        a.INVESTADVISORABBRNAME,
        a.INVESTADVISORNAME,
        ROW_NUMBER() OVER (
            PARTITION BY a.INVESTADVISORCODE
            ORDER BY COALESCE(a.XGRQ, '1900-01-01') DESC, a.ID DESC
        ) AS rn
    FROM mf_investadvisoroutline a
),
latest_type AS (
    SELECT
        t.INNERCODE,
        t.SECASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.ID DESC
        ) AS rn
    FROM mf_jyfundtype t
    CROSS JOIN params p
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= p.observation_date
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.observation_date)
      AND t.INSERTTIME < p.visible_cutoff
),
main_product AS (
    SELECT DISTINCT
        f.SECURITYCODE AS product_code,
        f.INVESTADVISORCODE
    FROM mf_fundarchives f
    JOIN secumain sm
      ON sm.INNERCODE = f.INNERCODE
     AND sm.SECUCATEGORY = 8
    CROSS JOIN params p
    WHERE f.MAINCODE = f.SECURITYCODE
      AND f.ESTABLISHMENTDATE <= p.observation_date
      AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE > p.observation_date)
),
fund_snapshot AS (
    SELECT
        f.INNERCODE,
        mp.INVESTADVISORCODE,
        mp.product_code,
        CASE WHEN f.IFFOF = 1 THEN 1 ELSE 0 END AS is_fof,
        CASE
            WHEN COALESCE(f.IFFOF, 2) <> 1
             AND f.INVESTMENTTYPE = 16
             AND lt.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
            THEN 1 ELSE 0
        END AS is_active_equity
    FROM mf_fundarchives f
    JOIN secumain sm
      ON sm.INNERCODE = f.INNERCODE
    JOIN main_product mp
      ON mp.product_code = LPAD(CAST(f.MAINCODE AS CHAR), 6, '0')
    LEFT JOIN latest_type lt
      ON lt.INNERCODE = f.INNERCODE
     AND lt.rn = 1
    CROSS JOIN params p
    WHERE sm.SECUCATEGORY = 8
      AND f.MAINCODE IS NOT NULL
      AND TRIM(CAST(f.MAINCODE AS CHAR)) <> ''
      AND f.ESTABLISHMENTDATE <= p.observation_date
      AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE > p.observation_date)
),
mgr_stats AS (
    SELECT
        fs.INVESTADVISORCODE,
        cm.PERSONALCODE,
        MAX(cm.NAME) AS manager_name_raw,
        COUNT(DISTINCT CASE WHEN fs.is_fof = 1 THEN fs.product_code END) AS fof_product_count,
        COUNT(DISTINCT CASE WHEN fs.is_active_equity = 1 THEN fs.product_code END) AS active_equity_product_count
    FROM current_mgr cm
    JOIN fund_snapshot fs
      ON fs.INNERCODE = cm.INNERCODE
    WHERE cm.rn = 1
    GROUP BY fs.INVESTADVISORCODE, cm.PERSONALCODE
)
SELECT
    COALESCE(al.INVESTADVISORABBRNAME, al.INVESTADVISORNAME) AS advisor_name,
    ms.PERSONALCODE AS manager_code,
    COALESCE(pl.CHINESENAME, ms.manager_name_raw) AS manager_name,
    ms.fof_product_count,
    ms.active_equity_product_count
FROM mgr_stats ms
LEFT JOIN person_latest pl
  ON pl.PERSONALCODE = ms.PERSONALCODE
 AND pl.rn = 1
LEFT JOIN advisor_latest al
  ON al.INVESTADVISORCODE = ms.INVESTADVISORCODE
 AND al.rn = 1
WHERE ms.fof_product_count > 0
  AND ms.active_equity_product_count > 0
ORDER BY ms.fof_product_count DESC,
         ms.active_equity_product_count DESC,
         advisor_name,
         manager_name;
