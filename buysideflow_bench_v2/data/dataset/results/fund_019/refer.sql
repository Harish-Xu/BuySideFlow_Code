WITH params AS (
    SELECT
        DATE('2025-08-15') AS asof_day,
        DATE('2025-08-16') AS asof_next_day
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
    JOIN params p
      ON 1 = 1
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= p.asof_day
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > p.asof_day)
      AND t.INSERTTIME < p.asof_next_day
),
fund_universe AS (
    SELECT
        fa.INNERCODE,
        fa.INVESTADVISORCODE,
        fa.SECURITYCODE AS fund_code
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN type_asof ta
      ON ta.INNERCODE = fa.INNERCODE
     AND ta.rn = 1
    JOIN params p
      ON 1 = 1
    WHERE ta.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
      AND fa.TYPE IN (2, 3, 4, 7, 8)
      AND fa.INVESTMENTTYPE = 16
      AND COALESCE(fa.IFFOF, 2) <> 1
      AND fa.INVESTADVISORCODE IS NOT NULL
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= p.asof_day
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > p.asof_day)
),
scale_versions AS (
    SELECT
        sa.INNERCODE,
        sa.ENDDATE,
        sa.COMBNVI,
        ROW_NUMBER() OVER (
            PARTITION BY sa.INNERCODE, sa.ENDDATE
            ORDER BY sa.INSERTTIME DESC, sa.ID DESC, sa.JSID DESC
        ) AS rn
    FROM mf_scaleanalysis sa
    JOIN params p
      ON 1 = 1
    WHERE sa.ENDDATE <= p.asof_day
      AND sa.INSERTTIME < p.asof_next_day
),
scale_latest_enddate AS (
    -- Select a visible version before looking back for a known merged scale.
    SELECT INNERCODE, MAX(ENDDATE) AS max_enddate
    FROM scale_versions
    WHERE rn = 1 AND COMBNVI IS NOT NULL
    GROUP BY INNERCODE
),
latest_scale AS (
    SELECT sa.INNERCODE, sa.COMBNVI
    FROM scale_versions sa
    JOIN scale_latest_enddate sle
      ON sle.INNERCODE = sa.INNERCODE
     AND sle.max_enddate = sa.ENDDATE
    WHERE sa.rn = 1
),
company_aum_top10 AS (
    SELECT
        fa.INVESTADVISORCODE,
        SUM(ls.COMBNVI) AS total_aum
    FROM mf_fundarchives fa
    JOIN latest_scale ls
      ON ls.INNERCODE = fa.INNERCODE
    CROSS JOIN params p
    WHERE fa.INVESTADVISORCODE IS NOT NULL
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= p.asof_day
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > p.asof_day)
    GROUP BY fa.INVESTADVISORCODE
    ORDER BY total_aum DESC, fa.INVESTADVISORCODE ASC
    LIMIT 10
),
holder_latest_enddate AS (
    SELECT
        h.INNERCODE,
        MAX(h.ENDDATE) AS max_enddate
    FROM mf_holderinfo h
    JOIN fund_universe fu
      ON fu.INNERCODE = h.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE DATE_FORMAT(h.ENDDATE, '%m-%d') = '06-30'
      AND h.ENDDATE <= p.asof_day
      AND h.INFOPUBLDATE < p.asof_next_day
    GROUP BY h.INNERCODE
),
latest_holder_h1 AS (
    SELECT
        x.INNERCODE,
        x.INSTITUTIONHOLDRATIO
    FROM (
        SELECT
            h.INNERCODE,
            h.INSTITUTIONHOLDRATIO,
            ROW_NUMBER() OVER (
                PARTITION BY h.INNERCODE
                ORDER BY h.INFOPUBLDATE DESC, h.INSERTTIME DESC, h.ID DESC, h.JSID DESC
            ) AS rn
        FROM mf_holderinfo h
        JOIN holder_latest_enddate hle
          ON hle.INNERCODE = h.INNERCODE
         AND hle.max_enddate = h.ENDDATE
        CROSS JOIN params p
        WHERE h.INFOPUBLDATE < p.asof_next_day
    ) x
    WHERE x.rn = 1
),
trade_latest_reportdate AS (
    SELECT
        ft.INNERCODE,
        MAX(ft.REPORTDATE) AS max_reportdate
    FROM mf_fundtradeinfo ft
    JOIN fund_universe fu
      ON fu.INNERCODE = ft.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE ft.REPORTDATE <= p.asof_day
      AND ft.INFOPUBLDATE < p.asof_next_day
    GROUP BY ft.INNERCODE
),
latest_trade AS (
    SELECT
        x.INNERCODE,
        x.TURNOVERRATE
    FROM (
        SELECT
            ft.INNERCODE,
            ft.TURNOVERRATE,
            ROW_NUMBER() OVER (
                PARTITION BY ft.INNERCODE
                ORDER BY ft.INFOPUBLDATE DESC, ft.INSERTTIME DESC, ft.ID DESC, ft.JSID DESC
            ) AS rn
        FROM mf_fundtradeinfo ft
        JOIN trade_latest_reportdate tlr
          ON tlr.INNERCODE = ft.INNERCODE
         AND tlr.max_reportdate = ft.REPORTDATE
        CROSS JOIN params p
        WHERE ft.INFOPUBLDATE < p.asof_next_day
    ) x
    WHERE x.rn = 1
)
SELECT
    fu.fund_code AS fund_code,
    COALESCE(sm.SECUABBR, sm.CHINAMEABBR) AS fund_name,
    ROUND(ls.COMBNVI / 100000000, 2) AS latest_scale_yi,
    ROUND(cat.total_aum / 100000000, 2) AS company_total_aum_yi,
    ROUND(lhh.INSTITUTIONHOLDRATIO,2) AS inst_holding_ratio,
    ROUND(lt.TURNOVERRATE * 100,2) AS half_year_turnover
FROM fund_universe fu
JOIN latest_scale ls
  ON ls.INNERCODE = fu.INNERCODE
JOIN company_aum_top10 cat
  ON cat.INVESTADVISORCODE = fu.INVESTADVISORCODE
JOIN latest_holder_h1 lhh
  ON lhh.INNERCODE = fu.INNERCODE
JOIN latest_trade lt
  ON lt.INNERCODE = fu.INNERCODE
JOIN secumain sm
  ON sm.INNERCODE = fu.INNERCODE
WHERE ls.COMBNVI BETWEEN 500000000 AND 2000000000
  AND lhh.INSTITUTIONHOLDRATIO BETWEEN 50 AND 70
  AND lt.TURNOVERRATE < 1
ORDER BY ls.COMBNVI DESC, fu.fund_code ASC;
