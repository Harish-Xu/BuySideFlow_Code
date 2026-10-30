WITH params AS (
    SELECT
        DATE('2026-02-27') AS as_of_date,
        DATE_ADD(DATE('2026-02-27'), INTERVAL 1 DAY) AS visible_cutoff,
        DATE('2022-12-31') AS start_date,
        DATE('2025-12-31') AS end_date
),
fund_archive AS (
    SELECT
        fa.INNERCODE
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN params p
      ON 1 = 1
    WHERE fa.SECURITYCODE = '005739'
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= p.end_date
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.start_date)
      AND (fa.INSERTTIME IS NULL OR fa.INSERTTIME < p.visible_cutoff)
    ORDER BY fa.INNERCODE
    LIMIT 1
),
portfolio_ranked AS (
    SELECT
        k.INNERCODE,
        k.REPORTDATE,
        k.SERIALNUMBER,
        k.RATIOINNV,
        ROW_NUMBER() OVER (
            PARTITION BY k.INNERCODE, k.REPORTDATE, k.SERIALNUMBER
            ORDER BY k.INFOPUBLDATE DESC, k.ID DESC
        ) AS rn
    FROM mf_keystockportfolio k
    JOIN params p
      ON k.REPORTDATE BETWEEN p.start_date AND p.end_date
     AND k.INFOPUBLDATE IS NOT NULL
     AND k.INFOPUBLDATE < p.visible_cutoff
    WHERE DATE_FORMAT(k.REPORTDATE, '%m%d') IN ('0331', '0630', '0930', '1231')
      AND k.SERIALNUMBER BETWEEN 1 AND 10
      AND k.RATIOINNV IS NOT NULL
),
portfolio_rows AS (
    SELECT
        INNERCODE,
        REPORTDATE,
        SERIALNUMBER,
        RATIOINNV
    FROM portfolio_ranked
    WHERE rn = 1
),
target_periods AS (
    SELECT DISTINCT
        pr.REPORTDATE
    FROM portfolio_rows pr
    JOIN fund_archive tf
      ON tf.INNERCODE = pr.INNERCODE
),
target_type_ranked AS (
    SELECT
        tp.REPORTDATE,
        jy.SECASSETCATCODE,
        jy.SECASSETCATNAME,
        ROW_NUMBER() OVER (
            PARTITION BY tp.REPORTDATE
            ORDER BY jy.EFFECTIVEDATE DESC, jy.INSERTTIME DESC, jy.ID DESC
        ) AS rn
    FROM target_periods tp
    JOIN fund_archive tf
      ON 1 = 1
    JOIN mf_jyfundtype jy
      ON jy.INNERCODE = tf.INNERCODE
    JOIN params p
      ON jy.INSERTTIME < p.visible_cutoff
    WHERE jy.STANDARD = 75
      AND jy.EFFECTIVEDATE <= tp.REPORTDATE
      AND (jy.CANCELDATE IS NULL OR jy.CANCELDATE > tp.REPORTDATE)
),
target_type AS (
    SELECT
        REPORTDATE,
        SECASSETCATCODE
    FROM target_type_ranked
    WHERE rn = 1
),
fund_universe AS (
    SELECT
        fa.INNERCODE,
        fa.ESTABLISHMENTDATE,
        fa.EXPIREDATE
    FROM mf_fundarchives fa
    JOIN params p
      ON fa.INSERTTIME IS NULL OR fa.INSERTTIME < p.visible_cutoff
    WHERE fa.MAINCODE = fa.SECURITYCODE
),
peer_type_ranked AS (
    SELECT
        tt.REPORTDATE,
        jy.INNERCODE,
        jy.SECASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY tt.REPORTDATE, jy.INNERCODE
            ORDER BY jy.EFFECTIVEDATE DESC, jy.INSERTTIME DESC, jy.ID DESC
        ) AS rn
    FROM target_type tt
    JOIN mf_jyfundtype jy
      ON jy.STANDARD = 75
     AND jy.EFFECTIVEDATE <= tt.REPORTDATE
     AND (jy.CANCELDATE IS NULL OR jy.CANCELDATE > tt.REPORTDATE)
    JOIN params p
      ON jy.INSERTTIME < p.visible_cutoff
),
scale_ranked AS (
    SELECT
        sa.INNERCODE,
        sa.ENDDATE,
        sa.FUNDREMARK,
        ROW_NUMBER() OVER (
            PARTITION BY sa.INNERCODE, sa.ENDDATE
            ORDER BY sa.INSERTTIME DESC, sa.ID DESC
        ) AS rn
    FROM mf_scaleanalysis sa
    JOIN params p
      ON sa.ENDDATE BETWEEN p.start_date AND p.end_date
     AND sa.INSERTTIME < p.visible_cutoff
),
peer_pool AS (
    SELECT DISTINCT
        ptr.REPORTDATE,
        ptr.INNERCODE
    FROM peer_type_ranked ptr
    JOIN target_type tt
      ON tt.REPORTDATE = ptr.REPORTDATE
     AND tt.SECASSETCATCODE = ptr.SECASSETCATCODE
    JOIN fund_universe fu
      ON fu.INNERCODE = ptr.INNERCODE
     AND fu.ESTABLISHMENTDATE <= ptr.REPORTDATE
     AND (fu.EXPIREDATE IS NULL OR fu.EXPIREDATE >= ptr.REPORTDATE)
    JOIN scale_ranked sr
      ON sr.INNERCODE = ptr.INNERCODE
     AND sr.ENDDATE = ptr.REPORTDATE
     AND sr.rn = 1
     AND sr.FUNDREMARK IN (1, 2, 3)
    WHERE ptr.rn = 1
),
fund_top10 AS (
    SELECT
        pr.REPORTDATE,
        SUM(pr.RATIOINNV) AS fund_top10_ratio
    FROM portfolio_rows pr
    JOIN fund_archive tf
      ON tf.INNERCODE = pr.INNERCODE
    GROUP BY pr.REPORTDATE
),
peer_top10 AS (
    SELECT
        pp.REPORTDATE,
        pp.INNERCODE,
        SUM(pr.RATIOINNV) AS top10_ratio
    FROM peer_pool pp
    JOIN portfolio_rows pr
      ON pr.INNERCODE = pp.INNERCODE
     AND pr.REPORTDATE = pp.REPORTDATE
    GROUP BY pp.REPORTDATE, pp.INNERCODE
),
peer_avg AS (
    SELECT
        REPORTDATE,
        AVG(top10_ratio) AS peer_avg_top10_ratio,
        COUNT(*) AS peer_sample_count
    FROM peer_top10
    GROUP BY REPORTDATE
)
SELECT
    DATE_FORMAT(ft.REPORTDATE, '%Y-%m-%d') AS `报告期`,
    ROUND(ft.fund_top10_ratio * 100, 2) AS `本基金集中度(%)`,
    ROUND(pa.peer_avg_top10_ratio * 100, 2) AS `同类平均集中度(%)`,
    pa.peer_sample_count AS `同类样本数`
FROM fund_top10 ft
LEFT JOIN peer_avg pa
  ON pa.REPORTDATE = ft.REPORTDATE
ORDER BY ft.REPORTDATE;
