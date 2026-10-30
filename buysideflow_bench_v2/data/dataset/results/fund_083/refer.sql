WITH anchors AS (
    SELECT DATE('2024-03-28') AS anchor_day, 'latest' AS period_label
    UNION ALL SELECT DATE('2023-03-28'), 'previous'
),
type_asof AS (
    SELECT t.INNERCODE, t.SECASSETCATCODE,
           ROW_NUMBER() OVER (PARTITION BY t.INNERCODE
               ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC) AS rn
    FROM mf_jyfundtype t
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= '2024-03-28'
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > '2024-03-28')
      AND t.INSERTTIME < '2024-03-29'
),
fund_pool AS (
    SELECT f.INNERCODE, sm.SECUCODE, sm.SECUABBR
    FROM mf_fundarchives f
    JOIN secumain sm ON sm.INNERCODE = f.INNERCODE
    JOIN type_asof t ON t.INNERCODE = f.INNERCODE AND t.rn = 1
    WHERE t.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
      AND f.TYPE IN (2, 3, 4, 7, 8)
      AND f.INVESTMENTTYPE = 16 AND COALESCE(f.IFFOF, 2) <> 1
      AND f.MAINCODE = f.SECURITYCODE
      AND f.ESTABLISHMENTDATE <= '2024-03-28'
      AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE > '2024-03-28')
),
holder_versions AS (
    SELECT h.INNERCODE, a.period_label, h.ENDDATE, h.INDIVIDUALHOLDRATIO,
           ROW_NUMBER() OVER (PARTITION BY h.INNERCODE, a.period_label, h.ENDDATE
               ORDER BY h.INFOPUBLDATE DESC, h.INSERTTIME DESC, h.ID DESC) AS version_rank
    FROM fund_pool f JOIN mf_holderinfo h ON h.INNERCODE = f.INNERCODE
    CROSS JOIN anchors a
    WHERE h.ENDDATE <= a.anchor_day
      AND h.INFOPUBLDATE < DATE_ADD(a.anchor_day, INTERVAL 1 DAY)
),
holders AS (
    SELECT INNERCODE, period_label, INDIVIDUALHOLDRATIO,
           ROW_NUMBER() OVER (PARTITION BY INNERCODE, period_label ORDER BY ENDDATE DESC) AS rn
    FROM holder_versions WHERE version_rank = 1 AND INDIVIDUALHOLDRATIO IS NOT NULL
),
scale_versions AS (
    SELECT s.INNERCODE, a.period_label, s.ENDDATE, s.TOTALASSET,
           ROW_NUMBER() OVER (PARTITION BY s.INNERCODE, a.period_label, s.ENDDATE
               ORDER BY s.INSERTTIME DESC, s.ID DESC) AS version_rank
    FROM fund_pool f JOIN mf_scaleanalysis s ON s.INNERCODE = f.INNERCODE
    CROSS JOIN anchors a
    WHERE s.ENDDATE <= a.anchor_day
      AND s.INSERTTIME < DATE_ADD(a.anchor_day, INTERVAL 1 DAY)
),
scales AS (
    SELECT INNERCODE, period_label, TOTALASSET,
           ROW_NUMBER() OVER (PARTITION BY INNERCODE, period_label ORDER BY ENDDATE DESC) AS rn
    FROM scale_versions WHERE version_rank = 1 AND TOTALASSET IS NOT NULL
)
SELECT f.SECUCODE AS fund_code, f.SECUABBR AS fund_name,
       ROUND(s.TOTALASSET / 100000000, 2) AS latest_total_asset_yi,
       ROUND((1 - s.TOTALASSET / p.TOTALASSET) * 100, 2) AS asset_decline_pct,
       ROUND(h.INDIVIDUALHOLDRATIO - hp.INDIVIDUALHOLDRATIO, 2) AS individual_ratio_increase_pp
FROM fund_pool f
JOIN scales s ON s.INNERCODE = f.INNERCODE AND s.period_label = 'latest' AND s.rn = 1
JOIN scales p ON p.INNERCODE = f.INNERCODE AND p.period_label = 'previous' AND p.rn = 1
JOIN holders h ON h.INNERCODE = f.INNERCODE AND h.period_label = 'latest' AND h.rn = 1
JOIN holders hp ON hp.INNERCODE = f.INNERCODE AND hp.period_label = 'previous' AND hp.rn = 1
WHERE s.TOTALASSET >= 1000000000 AND p.TOTALASSET > 0
  AND s.TOTALASSET <= p.TOTALASSET * 0.8
  AND h.INDIVIDUALHOLDRATIO BETWEEN 0 AND 100
  AND hp.INDIVIDUALHOLDRATIO BETWEEN 0 AND 100
  AND h.INDIVIDUALHOLDRATIO - hp.INDIVIDUALHOLDRATIO >= 20
ORDER BY h.INDIVIDUALHOLDRATIO - hp.INDIVIDUALHOLDRATIO DESC,
         f.SECUCODE;
