WITH anchor_date AS (
    SELECT DATE('2025-09-01') AS anchor_day
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
fund_pool AS (
    SELECT
        f.INNERCODE,
        sm.SECUCODE,
        sm.SECUABBR
    FROM mf_fundarchives f
    JOIN type_asof ta
      ON ta.INNERCODE = f.INNERCODE
     AND ta.rn = 1 AND ta.SECASSETCATCODE = 1201
    JOIN secumain sm
      ON sm.INNERCODE = f.INNERCODE
    JOIN anchor_date ad
      ON 1 = 1
    WHERE f.TYPE IN (2, 3, 4, 7, 8)
      AND f.MAINCODE = f.SECURITYCODE
      AND COALESCE(f.IFINITIATINGFUND, 1) = 2
      AND f.ESTABLISHMENTDATE <= ad.anchor_day
      AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE > ad.anchor_day)
),
latest_holder AS (
    SELECT
        h.INNERCODE,
        h.ENDDATE,
        h.INSTITUTIONHOLDRATIO AS institution_hold_ratio_pct,
        ROW_NUMBER() OVER (
            PARTITION BY h.INNERCODE
            ORDER BY h.ENDDATE DESC, h.INFOPUBLDATE DESC, h.ID DESC
        ) AS rn
    FROM mf_holderinfo h
    JOIN fund_pool fp
      ON fp.INNERCODE = h.INNERCODE
    JOIN anchor_date ad
      ON 1 = 1
    WHERE h.ENDDATE <= ad.anchor_day
      AND h.INFOPUBLDATE < DATE_ADD(ad.anchor_day, INTERVAL 1 DAY)
)
SELECT
    fp.SECUCODE AS fund_code,
    fp.SECUABBR AS fund_name,
    lh.ENDDATE AS holder_date,
    ROUND(lh.institution_hold_ratio_pct, 2) AS institution_hold_ratio_pct
FROM fund_pool fp
JOIN latest_holder lh
  ON lh.INNERCODE = fp.INNERCODE
 AND lh.rn = 1
WHERE lh.institution_hold_ratio_pct BETWEEN 0 AND 100
ORDER BY lh.institution_hold_ratio_pct DESC, fund_code
LIMIT 10;
