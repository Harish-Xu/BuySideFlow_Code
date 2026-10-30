WITH report_params AS (
    SELECT DATE('2025-12-31') AS report_date,
           DATE('2026-02-24') AS data_cutoff
),
type_asof AS (
    SELECT t.INNERCODE,
           t.SECASSETCATCODE,
           ROW_NUMBER() OVER (
               PARTITION BY t.INNERCODE
               ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
           ) AS rn
    FROM mf_jyfundtype t
    JOIN report_params rp
      ON t.EFFECTIVEDATE <= rp.report_date
     AND (t.CANCELDATE IS NULL OR t.CANCELDATE > rp.report_date)
     AND t.INSERTTIME < DATE_ADD(rp.data_cutoff, INTERVAL 1 DAY)
    WHERE t.STANDARD = 75
),
fund_pool AS (
    SELECT fa.INNERCODE
    FROM mf_fundarchives fa
    JOIN secumain fs
      ON fs.INNERCODE = fa.INNERCODE
     AND fs.SECUCATEGORY = 8
    JOIN type_asof t
      ON t.INNERCODE = fa.INNERCODE
     AND t.rn = 1
    JOIN report_params rp
      ON 1 = 1
    WHERE fa.MAINCODE IS NOT NULL
      AND TRIM(CAST(fa.MAINCODE AS CHAR)) <> ''
      AND LPAD(CAST(fa.MAINCODE AS CHAR), 6, '0') = fa.SECURITYCODE
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND fa.INVESTMENTTYPE = 16
      -- Dated disclosed holdings establish membership when archive dates are missing.
      AND (fa.ESTABLISHMENTDATE IS NULL OR fa.ESTABLISHMENTDATE <= rp.report_date)
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= rp.report_date)
      AND t.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
),
holding_versions AS (
    SELECT k.INNERCODE,
           k.REPORTDATE,
           k.INVESTTYPE,
           k.SERIALNUMBER,
           k.STOCKINNERCODE,
           k.MARKETVALUE,
           ROW_NUMBER() OVER (
               PARTITION BY k.INNERCODE, k.REPORTDATE, k.INVESTTYPE, k.SERIALNUMBER
               ORDER BY COALESCE(k.INFOPUBLDATE, k.INSERTTIME) DESC,
                        k.INSERTTIME DESC,
                        k.ID DESC
           ) AS rn
    FROM mf_keystockportfolio k
    JOIN fund_pool fp
      ON fp.INNERCODE = k.INNERCODE
    JOIN report_params rp
      ON k.REPORTDATE >= rp.report_date
     AND k.REPORTDATE < DATE_ADD(rp.report_date, INTERVAL 1 DAY)
    WHERE k.SERIALNUMBER <= 10
      AND k.STOCKINNERCODE IS NOT NULL
      AND k.MARKETVALUE IS NOT NULL
      AND (
            (k.INFOPUBLDATE IS NOT NULL
             AND k.INFOPUBLDATE < DATE_ADD(rp.data_cutoff, INTERVAL 1 DAY))
         OR (k.INFOPUBLDATE IS NULL
             AND k.INSERTTIME < DATE_ADD(rp.data_cutoff, INTERVAL 1 DAY))
      )
),
holdings AS (
    SELECT INNERCODE,
           STOCKINNERCODE,
           MARKETVALUE
    FROM holding_versions
    WHERE rn = 1
),
stock_candidates AS (
    SELECT DISTINCT STOCKINNERCODE
    FROM holdings
),
stock_map AS (
    SELECT sc.STOCKINNERCODE,
           sm.SECUCODE,
           sm.SECUABBR
    FROM stock_candidates sc
    JOIN secumain sm
      ON sm.INNERCODE = sc.STOCKINNERCODE
    WHERE sc.STOCKINNERCODE < 1000000
    UNION ALL
    SELECT sc.STOCKINNERCODE,
           hk.SECUCODE,
           hk.SECUABBR
    FROM stock_candidates sc
    JOIN hk_secumain hk
      ON hk.INNERCODE = sc.STOCKINNERCODE
    WHERE sc.STOCKINNERCODE >= 1000000
      AND sc.STOCKINNERCODE < 2000000
)
SELECT sm.SECUCODE AS `股票代码`,
       sm.SECUABBR AS `简称`,
       ROUND(SUM(h.MARKETVALUE) / 100000000, 2) AS `合计持仓市值（亿元）`
FROM holdings h
JOIN stock_map sm
  ON sm.STOCKINNERCODE = h.STOCKINNERCODE
GROUP BY sm.STOCKINNERCODE, sm.SECUCODE, sm.SECUABBR
ORDER BY SUM(h.MARKETVALUE) DESC, sm.SECUCODE ASC
LIMIT 10;
