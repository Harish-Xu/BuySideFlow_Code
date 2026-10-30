WITH params AS (
    SELECT DATE('2025-12-30') AS as_of_date
),
base_candidates AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        sm.SECUABBR AS fund_name,
        fa.INVESTORIENTATION
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN params p
      ON 1 = 1
    WHERE fa.ESTABLISHMENTDATE <= p.as_of_date
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.as_of_date)
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND fa.MAINCODE = fa.SECURITYCODE
      AND COALESCE(fa.INVESTORIENTATION, '') LIKE '%行业景气度%'
      AND (fa.INSERTTIME IS NULL OR fa.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
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
    JOIN base_candidates bc
      ON bc.INNERCODE = t.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= p.as_of_date
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.as_of_date)
      AND t.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
),
standard_stock_funds AS (
    SELECT
        bc.INNERCODE,
        bc.fund_code,
        bc.fund_name,
        bc.INVESTORIENTATION
    FROM base_candidates bc
    JOIN type_asof ta
      ON ta.INNERCODE = bc.INNERCODE
     AND ta.rn = 1
    WHERE ta.SECASSETCATCODE = 1101
),
latest_scale AS (
    SELECT
        x.INNERCODE,
        x.COMBNVI AS latest_scale
    FROM (
        SELECT
            s.INNERCODE,
            s.ENDDATE,
            s.COMBNVI,
            ROW_NUMBER() OVER (
                PARTITION BY s.INNERCODE
                ORDER BY s.ENDDATE DESC, s.INSERTTIME DESC, s.ID DESC
            ) AS rn
        FROM mf_scaleanalysis s
        JOIN standard_stock_funds f
          ON f.INNERCODE = s.INNERCODE
        JOIN params p
          ON 1 = 1
        WHERE s.ENDDATE <= p.as_of_date
          AND s.COMBNVI IS NOT NULL
          AND s.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    ) x
    WHERE x.rn = 1
)
SELECT
    f.fund_code AS `基金代码`,
    f.fund_name AS `基金简称`,
    ROUND(ls.latest_scale / 100000000, 2) AS `最新基金规模(亿元)`
FROM standard_stock_funds f
LEFT JOIN latest_scale ls
  ON ls.INNERCODE = f.INNERCODE
ORDER BY f.fund_code;
