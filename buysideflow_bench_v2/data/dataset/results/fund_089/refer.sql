WITH params AS (
    SELECT
        DATE('2026-01-21') AS as_of_date,
        DATE('2025-12-31') AS report_date
),
type_asof AS (
    SELECT INNERCODE, SECASSETCATCODE
    FROM (
        SELECT
            t.INNERCODE,
            t.SECASSETCATCODE,
            ROW_NUMBER() OVER (
                PARTITION BY t.INNERCODE
                ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.JSID DESC, t.ID DESC
            ) AS rn
        FROM mf_jyfundtype t
        JOIN params p
          ON t.EFFECTIVEDATE <= p.as_of_date
         AND t.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE t.STANDARD = 75
          AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.as_of_date)
    ) ranked
    WHERE rn = 1
),
fund_pool AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        COALESCE(NULLIF(s.SECUABBR, ''), NULLIF(s.CHINAMEABBR, ''), s.CHINAME) AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain s
      ON s.INNERCODE = fa.INNERCODE
     AND s.SECUCATEGORY = 8
    JOIN type_asof t
      ON t.INNERCODE = fa.INNERCODE
    JOIN params p
      ON fa.ESTABLISHMENTDATE <= p.as_of_date
     AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > p.as_of_date)
    WHERE fa.MAINCODE = fa.SECURITYCODE
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND fa.INVESTMENTTYPE = 16
      AND t.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
),
holding_versions AS (
    SELECT
        k.INNERCODE,
        k.SERIALNUMBER,
        k.STOCKINNERCODE,
        k.RATIOINNV,
        ROW_NUMBER() OVER (
            PARTITION BY k.INNERCODE, k.REPORTDATE, k.INVESTTYPE, k.SERIALNUMBER
            ORDER BY k.INFOPUBLDATE DESC, k.INSERTTIME DESC, k.JSID DESC, k.ID DESC
        ) AS rn
    FROM mf_keystockportfolio k
    JOIN fund_pool fp
      ON fp.INNERCODE = k.INNERCODE
    JOIN params p
      ON k.REPORTDATE >= p.report_date
     AND k.REPORTDATE < DATE_ADD(p.report_date, INTERVAL 1 DAY)
    WHERE k.INVESTTYPE = 1
      AND k.SERIALNUMBER BETWEEN 1 AND 10
      AND k.STOCKINNERCODE IS NOT NULL
      AND k.RATIOINNV IS NOT NULL
      AND k.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND (k.INSERTTIME IS NULL OR k.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
),
holdings AS (
    SELECT INNERCODE, STOCKINNERCODE, RATIOINNV
    FROM holding_versions
    WHERE rn = 1
),
board_ratio AS (
    SELECT
        h.INNERCODE,
        SUM(CASE WHEN s.LISTEDSECTOR = 6 THEN h.RATIOINNV ELSE 0 END) AS gem_ratio,
        SUM(CASE WHEN s.LISTEDSECTOR = 7 THEN h.RATIOINNV ELSE 0 END) AS star_ratio
    FROM holdings h
    LEFT JOIN secumain s
      ON s.INNERCODE = h.STOCKINNERCODE
     AND s.SECUCATEGORY = 1
    GROUP BY h.INNERCODE
)
SELECT
    fp.fund_code AS `基金代码`,
    fp.fund_name AS `简称`,
    ROUND(b.gem_ratio * 100, 2) AS `创业板占比`,
    ROUND(b.star_ratio * 100, 2) AS `科创板占比`,
    ROUND((b.gem_ratio + b.star_ratio) * 100, 2) AS `双创合计占比`
FROM board_ratio b
JOIN fund_pool fp
  ON fp.INNERCODE = b.INNERCODE
ORDER BY b.gem_ratio + b.star_ratio DESC, fp.fund_code ASC
LIMIT 10;
