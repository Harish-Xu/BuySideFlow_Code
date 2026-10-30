WITH
anchor_date AS (
    SELECT DATE('2026-01-05') AS anchor_day,
           TIMESTAMP('2026-01-06 00:00:00') AS visible_cutoff
),
latest_type AS (
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
      AND t.INSERTTIME < ad.visible_cutoff
),
qdii_funds AS (
    SELECT
        f.INNERCODE,
        sm.SECUCODE,
        sm.SECUABBR,
        f.PERFORMANCEBENCHMARK
    FROM mf_fundarchives f
    JOIN secumain sm
        ON sm.INNERCODE = f.INNERCODE
    JOIN latest_type lt
        ON lt.INNERCODE = f.INNERCODE
       AND lt.rn = 1
    JOIN anchor_date ad
        ON 1 = 1
    WHERE lt.firstassetcatcode = 15
      AND sm.SECUCATEGORY = 8
      AND COALESCE(f.IFFOF, 2) <> 1
      AND f.ESTABLISHMENTDATE <= ad.anchor_day
      AND (f.EXPIREDATE IS NULL OR f.EXPIREDATE > ad.anchor_day)
      AND f.PerformanceBenchMark LIKE '%MSCI World Index%'
),
latest_pr AS (
    SELECT
        p.INNERCODE,
        p.ENDDATE,
        p.APPLYINGTYPE,
        p.REDEEMTYPE,
        p.LARGEAPPLYINGMAX,
        ROW_NUMBER() OVER (
            PARTITION BY p.INNERCODE
            ORDER BY p.ENDDATE DESC, p.INSERTTIME DESC, p.ID DESC
        ) AS rn
    FROM mf_purchaseandredeem p
    JOIN anchor_date ad
        ON 1 = 1
    WHERE p.ENDDATE <= ad.anchor_day
      AND p.INSERTTIME < ad.visible_cutoff
),
candidate_pool AS (
    SELECT
        qf.SECUCODE AS fund_code,
        qf.SECUABBR AS fund_name,
        pr.ENDDATE AS status_date,
        pr.APPLYINGTYPE AS applying_type,
        pr.REDEEMTYPE AS redeem_type,
        qf.PERFORMANCEBENCHMARK AS performance_benchmark,
        pr.LARGEAPPLYINGMAX AS large_applying_max_wanyuan,
        CASE
            WHEN pr.APPLYINGTYPE = '可申购' AND pr.REDEEMTYPE = '可赎回' THEN 'primary_open_purchase'
            WHEN pr.APPLYINGTYPE = '限大额'
             AND pr.REDEEMTYPE = '可赎回'
             AND pr.LARGEAPPLYINGMAX >= 500 THEN 'fallback_limited_purchase'
        END AS rule_used
    FROM qdii_funds qf
    JOIN latest_pr pr
        ON pr.INNERCODE = qf.INNERCODE
       AND pr.rn = 1
    WHERE (pr.APPLYINGTYPE = '可申购' AND pr.REDEEMTYPE = '可赎回')
       OR (
            pr.APPLYINGTYPE = '限大额'
        AND pr.REDEEMTYPE = '可赎回'
        AND pr.LARGEAPPLYINGMAX >= 500
       )
)
SELECT
    cp.rule_used,
    cp.fund_code,
    cp.fund_name,
    cp.status_date,
    cp.applying_type,
    cp.redeem_type,
    cp.performance_benchmark,
    cp.large_applying_max_wanyuan
FROM candidate_pool cp
ORDER BY COALESCE(cp.large_applying_max_wanyuan, -1) DESC, cp.fund_code ASC;
