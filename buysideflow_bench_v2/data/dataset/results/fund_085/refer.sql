WITH anchor_date AS (
    SELECT DATE('2025-03-17') AS anchor_day
),
type_asof AS (
    SELECT
        t.INNERCODE,
        t.FIRSTASSETCATCODE,
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
        fa.INNERCODE,
        sm.SECUCODE,
        sm.SECUABBR
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
    JOIN type_asof ta
      ON ta.INNERCODE = fa.INNERCODE
     AND ta.rn = 1
    JOIN anchor_date ad
      ON 1 = 1
    WHERE sm.SECUCATEGORY = 8
      AND fa.MAINCODE = fa.SECURITYCODE
      AND ta.FIRSTASSETCATCODE <> 14
      AND fa.ESTABLISHMENTDATE <= ad.anchor_day
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > ad.anchor_day)
),
latest_holder AS (
    SELECT
        h.INNERCODE,
        h.ENDDATE,
        h.HOLDERACCOUNTNUMBER,
        h.AVERAGEHOLDSHARES,
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
    lh.HOLDERACCOUNTNUMBER,
    lh.AVERAGEHOLDSHARES
FROM latest_holder lh
JOIN fund_pool fp
  ON fp.INNERCODE = lh.INNERCODE
WHERE lh.rn = 1
  AND lh.HOLDERACCOUNTNUMBER IS NOT NULL
  AND lh.AVERAGEHOLDSHARES IS NOT NULL
ORDER BY lh.HOLDERACCOUNTNUMBER DESC, lh.AVERAGEHOLDSHARES ASC, fp.SECUCODE ASC
LIMIT 1;
