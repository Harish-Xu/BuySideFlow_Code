WITH anchor_date AS (
    SELECT DATE('2025-12-31') AS anchor_day
),
type_asof AS (
    SELECT t.INNERCODE,
           t.SECASSETCATCODE,
           ROW_NUMBER() OVER (
               PARTITION BY t.INNERCODE
               ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
           ) AS rn
    FROM mf_jyfundtype t
    JOIN anchor_date ad
      ON t.EFFECTIVEDATE <= ad.anchor_day
     AND (t.CANCELDATE IS NULL OR t.CANCELDATE > ad.anchor_day)
    WHERE t.STANDARD = 75
      AND t.INSERTTIME < DATE_ADD(ad.anchor_day, INTERVAL 1 DAY)
),
active_equity_shares AS (
    SELECT f.INNERCODE,
           CASE
               WHEN f.MAINCODE IS NULL OR CAST(f.MAINCODE AS CHAR) = '' THEN f.SECURITYCODE
               ELSE LPAD(CAST(f.MAINCODE AS CHAR), 6, '0')
           END AS main_fund_code
    FROM mf_fundarchives f
    JOIN type_asof ta
      ON ta.INNERCODE = f.INNERCODE
     AND ta.rn = 1
    JOIN anchor_date ad
      ON 1 = 1
    WHERE ta.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
      AND f.INVESTMENTTYPE = 16
      AND COALESCE(f.IFFOF, 2) <> 1
      AND f.ESTABLISHMENTDATE IS NOT NULL
      AND DATE(f.ESTABLISHMENTDATE) <= ad.anchor_day
      AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE > ad.anchor_day)
),
manager_asof AS (
    SELECT m.INNERCODE,
           m.PERSONALCODE,
           m.NAME,
           m.ACCESSIONDATE,
           m.DIMISSIONDATE,
           ROW_NUMBER() OVER (
               PARTITION BY m.INNERCODE, m.PERSONALCODE
               ORDER BY m.ACCESSIONDATE DESC,
                        COALESCE(m.DIMISSIONDATE, DATE('9999-12-31')) DESC,
                        m.ID DESC
           ) AS rn
    FROM mf_fundmanagernew m
    JOIN anchor_date ad
      ON 1 = 1
    WHERE m.POSTNAME = 1
      AND m.ACCESSIONDATE IS NOT NULL
      AND DATE(m.ACCESSIONDATE) <= ad.anchor_day
      AND (m.DIMISSIONDATE IS NULL OR DATE(m.DIMISSIONDATE) > ad.anchor_day)
      AND m.INFOPUBLDATE < DATE_ADD(ad.anchor_day, INTERVAL 1 DAY)
),
share_latest_asset AS (
    SELECT s.INNERCODE,
           s.TOTALASSET,
           ROW_NUMBER() OVER (
               PARTITION BY s.INNERCODE
               ORDER BY s.ENDDATE DESC, s.INSERTTIME DESC, s.ID DESC
           ) AS rn
    FROM mf_scaleanalysis s
    JOIN anchor_date ad
      ON s.ENDDATE <= ad.anchor_day
    WHERE s.TOTALASSET IS NOT NULL
      AND s.INSERTTIME < DATE_ADD(ad.anchor_day, INTERVAL 1 DAY)
),
main_fund_asset AS (
    SELECT aes.main_fund_code,
           SUM(COALESCE(sla.TOTALASSET, 0)) AS main_fund_asset
    FROM active_equity_shares aes
    LEFT JOIN share_latest_asset sla
      ON sla.INNERCODE = aes.INNERCODE
     AND sla.rn = 1
    GROUP BY aes.main_fund_code
),
manager_main_fund AS (
    SELECT DISTINCT ma.PERSONALCODE,
           aes.main_fund_code
    FROM manager_asof ma
    JOIN active_equity_shares aes
      ON aes.INNERCODE = ma.INNERCODE
    WHERE ma.rn = 1
),
manager_name AS (
    SELECT x.PERSONALCODE,
           x.NAME AS manager_name
    FROM (
        SELECT ma.PERSONALCODE,
               ma.NAME,
               ROW_NUMBER() OVER (
                   PARTITION BY ma.PERSONALCODE
                   ORDER BY ma.ACCESSIONDATE DESC,
                            COALESCE(ma.DIMISSIONDATE, DATE('9999-12-31')) DESC,
                            ma.INNERCODE DESC
               ) AS rn
        FROM manager_asof ma
        WHERE ma.rn = 1
    ) x
    WHERE x.rn = 1
),
manager_total_asset AS (
    SELECT mmf.PERSONALCODE,
           SUM(COALESCE(mfa.main_fund_asset, 0)) AS manager_total_asset
    FROM manager_main_fund mmf
    LEFT JOIN main_fund_asset mfa
      ON mfa.main_fund_code = mmf.main_fund_code
    GROUP BY mmf.PERSONALCODE
),
top_manager AS (
    SELECT mta.PERSONALCODE,
           mta.manager_total_asset,
           DENSE_RANK() OVER (ORDER BY mta.manager_total_asset DESC) AS rk
    FROM manager_total_asset mta
),
main_fund_name AS (
    SELECT x.main_fund_code,
           x.main_fund_name
    FROM (
        SELECT aes.main_fund_code,
               sm.SECUABBR AS main_fund_name,
               ROW_NUMBER() OVER (
                   PARTITION BY aes.main_fund_code
                   ORDER BY CASE WHEN sm.SECUCODE = aes.main_fund_code THEN 0 ELSE 1 END,
                            sm.INNERCODE
               ) AS rn
        FROM active_equity_shares aes
        JOIN secumain sm
          ON sm.INNERCODE = aes.INNERCODE
         AND sm.SECUCATEGORY = 8
    ) x
    WHERE x.rn = 1
)
SELECT mn.manager_name AS `基金经理姓名`,
       ROUND(tm.manager_total_asset / 100000000, 2) AS `合计管理规模（亿元）`,
       mmf.main_fund_code AS `主基金代码`,
       mfn.main_fund_name AS `主基金简称`,
       ROUND(COALESCE(mfa.main_fund_asset, 0) / 100000000, 2) AS `主基金合并规模（亿元）`
FROM top_manager tm
JOIN manager_name mn
  ON mn.PERSONALCODE = tm.PERSONALCODE
JOIN manager_main_fund mmf
  ON mmf.PERSONALCODE = tm.PERSONALCODE
LEFT JOIN main_fund_asset mfa
  ON mfa.main_fund_code = mmf.main_fund_code
LEFT JOIN main_fund_name mfn
  ON mfn.main_fund_code = mmf.main_fund_code
WHERE tm.rk = 1
ORDER BY tm.manager_total_asset DESC,
         mn.manager_name,
         mfa.main_fund_asset DESC,
         mmf.main_fund_code;
