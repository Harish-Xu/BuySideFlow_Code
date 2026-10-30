WITH
params AS (
    SELECT
        DATE('2025-04-30') AS as_of_date,
        DATE_SUB(DATE('2025-04-30'), INTERVAL 1 YEAR) AS nav_start_date
),
fund_type_asof AS (
    SELECT
        t.INNERCODE
    FROM (
        SELECT
            ft.INNERCODE,
            ft.SECASSETCATCODE,
            ROW_NUMBER() OVER (
                PARTITION BY ft.INNERCODE
                ORDER BY ft.EFFECTIVEDATE DESC, ft.INSERTTIME DESC, ft.ID DESC
            ) AS rn
        FROM mf_jyfundtype ft
        JOIN params p
          ON ft.EFFECTIVEDATE <= p.as_of_date
        WHERE ft.STANDARD = 75
          AND (ft.CANCELDATE IS NULL OR ft.CANCELDATE > p.as_of_date)
          AND ft.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    ) t
    WHERE t.rn = 1
      AND t.SECASSETCATCODE = '1101'
),
fund_base AS (
    SELECT
        s.INNERCODE AS fund_innercode,
        s.SECUCODE AS fund_code,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS fund_name,
        fa.ESTABLISHMENTDATE
    FROM secumain s
    JOIN mf_fundarchives fa
      ON fa.INNERCODE = s.INNERCODE
    JOIN fund_type_asof fta
      ON fta.INNERCODE = s.INNERCODE
    JOIN params p
      ON fa.ESTABLISHMENTDATE <= DATE_SUB(p.as_of_date, INTERVAL 3 YEAR)
    WHERE s.SECUCATEGORY = 8
      AND fa.MAINCODE = fa.SECURITYCODE
      AND (fa.EXPIREDATE IS NULL OR DATE(fa.EXPIREDATE) > p.as_of_date)
),
scale_dedup AS (
    SELECT
        sa.INNERCODE,
        sa.ENDDATE,
        sa.TOTALASSET,
        ROW_NUMBER() OVER (
            PARTITION BY sa.INNERCODE, sa.ENDDATE
            ORDER BY sa.INSERTTIME DESC, sa.ID DESC
        ) AS rn
    FROM mf_scaleanalysis sa
    JOIN fund_base fb
      ON fb.fund_innercode = sa.INNERCODE
    JOIN params p
      ON sa.ENDDATE <= p.as_of_date
     AND sa.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE (MONTH(sa.ENDDATE) = 3 AND DAY(sa.ENDDATE) = 31)
       OR (MONTH(sa.ENDDATE) IN (6, 9) AND DAY(sa.ENDDATE) = 30)
       OR (MONTH(sa.ENDDATE) = 12 AND DAY(sa.ENDDATE) = 31)
),
scale_periods AS (
    -- Choose each fund's four latest disclosed quarter ends before testing values.
    -- IFSCALECOMB describes scale disclosure, not precedence for TOTALASSET revisions.
    SELECT
        INNERCODE,
        ENDDATE,
        TOTALASSET,
        ROW_NUMBER() OVER (
            PARTITION BY INNERCODE ORDER BY ENDDATE DESC
        ) AS period_rn
    FROM scale_dedup
    WHERE rn = 1
),
qualified_scale AS (
    SELECT
        sd.INNERCODE
    FROM scale_periods sd
    WHERE sd.period_rn <= 4
    GROUP BY sd.INNERCODE
    HAVING COUNT(DISTINCT sd.ENDDATE) = 4
       AND COUNT(sd.TOTALASSET) = 4
       AND AVG(sd.TOTALASSET) >= 2000000000
),
nav_raw AS (
    SELECT
        fb.fund_innercode,
        fb.fund_code,
        fb.fund_name,
        DATE(nv.TRADINGDAY) AS tradingday,
        nv.UNITNVRESTORED AS unit_nv_restored,
        ROW_NUMBER() OVER (
            PARTITION BY fb.fund_innercode, DATE(nv.TRADINGDAY)
            ORDER BY nv.INSERTTIME DESC, nv.ID DESC
        ) AS rn
    FROM fund_base fb
    JOIN qualified_scale qs
      ON qs.INNERCODE = fb.fund_innercode
    JOIN mf_fundnetvalueretrans nv
      ON nv.INNERCODE = fb.fund_innercode
    JOIN params p
      ON nv.TRADINGDAY BETWEEN p.nav_start_date AND p.as_of_date
    WHERE nv.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
),
nav_series AS (
    SELECT
        fund_innercode,
        fund_code,
        fund_name,
        tradingday,
        unit_nv_restored
    FROM nav_raw
    WHERE rn = 1
),
nav_extrema AS (
    SELECT
        ns.*,
        FIRST_VALUE(unit_nv_restored) OVER (
            PARTITION BY fund_innercode
            ORDER BY tradingday ASC
        ) AS start_nv,
        FIRST_VALUE(unit_nv_restored) OVER (
            PARTITION BY fund_innercode
            ORDER BY tradingday DESC
        ) AS end_nv,
        MAX(unit_nv_restored) OVER (
            PARTITION BY fund_innercode
            ORDER BY tradingday
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS running_peak,
        ROW_NUMBER() OVER (
            PARTITION BY fund_innercode
            ORDER BY tradingday DESC
        ) AS rn_desc
    FROM nav_series ns
),
nav_summary AS (
    SELECT
        fund_innercode,
        MAX(CASE WHEN rn_desc = 1 THEN fund_code END) AS fund_code,
        MAX(CASE WHEN rn_desc = 1 THEN fund_name END) AS fund_name,
        MAX(end_nv / NULLIF(start_nv, 0) - 1) AS return_1y,
        MIN(unit_nv_restored / NULLIF(running_peak, 0) - 1) AS max_drawdown_1y,
        MIN(tradingday) AS start_date,
        MAX(tradingday) AS end_date,
        COUNT(*) AS obs_cnt,
        SUM(CASE WHEN unit_nv_restored IS NULL OR unit_nv_restored <= 0
                 THEN 1 ELSE 0 END) AS invalid_obs_cnt
    FROM nav_extrema
    GROUP BY fund_innercode
),
qualified_nav AS (
    SELECT
        ns.fund_innercode,
        ns.fund_code,
        ns.fund_name,
        ns.return_1y,
        ns.max_drawdown_1y
    FROM nav_summary ns
    JOIN params p
      ON ns.start_date = p.nav_start_date
     AND ns.end_date = p.as_of_date
    WHERE ns.obs_cnt >= 2
      AND ns.invalid_obs_cnt = 0
      -- The reported drawdown is negative; a loss of at most 20% means >= -0.20.
      AND ns.max_drawdown_1y >= -0.20
)
SELECT
    qn.fund_code AS `基金代码`,
    qn.fund_name AS `基金名称`,
    ROUND(qn.return_1y * 100, 4) AS `过去1年收益率(%)`,
    ROUND(qn.max_drawdown_1y * 100, 4) AS `过去1年最大回撤(%)`
FROM qualified_nav qn
ORDER BY qn.return_1y DESC, qn.fund_code
LIMIT 20;
