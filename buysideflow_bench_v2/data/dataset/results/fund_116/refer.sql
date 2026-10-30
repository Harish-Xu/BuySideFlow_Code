WITH params AS (
    SELECT DATE('2026-02-08') AS anchor_day
),
target_fund AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        sm.SECUABBR AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
    WHERE fa.SECURITYCODE = '020397'
      AND sm.SECUCATEGORY = 8
      AND COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) = fa.SECURITYCODE
),
manager_target AS (
    SELECT
        m.INNERCODE,
        m.NAME AS manager_name,
        m.ACCESSIONDATE AS start_date
    FROM mf_fundmanagernew m
    JOIN target_fund tf
      ON tf.INNERCODE = m.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE m.NAME = '郑宁'
      AND m.POSTNAME = 1
      AND (m.INFOPUBLDATE IS NULL OR m.INFOPUBLDATE < DATE_ADD(p.anchor_day, INTERVAL 1 DAY))
      AND (m.ACCESSIONDATE IS NULL OR m.ACCESSIONDATE <= p.anchor_day)
      AND (m.DIMISSIONDATE IS NULL OR m.DIMISSIONDATE > p.anchor_day)
    ORDER BY m.ACCESSIONDATE ASC, m.ID ASC
    LIMIT 1
),
nav_dedup AS (
    SELECT
        n.INNERCODE,
        DATE(n.TRADINGDAY) AS trade_date,
        n.UNITNVRESTORED,
        ROW_NUMBER() OVER (
            PARTITION BY n.INNERCODE, DATE(n.TRADINGDAY)
            ORDER BY n.TRADINGDAY DESC, n.UPDATETIME DESC, n.INSERTTIME DESC, n.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans n
    JOIN manager_target mt
      ON mt.INNERCODE = n.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE n.UNITNVRESTORED IS NOT NULL
      AND n.TRADINGDAY >= mt.start_date
      AND n.TRADINGDAY <= p.anchor_day
      AND n.INSERTTIME < DATE_ADD(p.anchor_day, INTERVAL 1 DAY)
      AND n.UPDATETIME < DATE_ADD(p.anchor_day, INTERVAL 1 DAY)
),
bench_dedup AS (
    SELECT
        b.INNERCODE,
        DATE(b.ENDDATE) AS trade_date,
        b.BENCHGRFORSINCE,
        ROW_NUMBER() OVER (
            PARTITION BY b.INNERCODE, DATE(b.ENDDATE)
            ORDER BY b.ENDDATE DESC, b.UPDATATIME DESC, b.INSERTTIME DESC, b.JSID DESC
        ) AS rn
    FROM mf_benchmarkgrowthrate b
    JOIN manager_target mt
      ON mt.INNERCODE = b.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE b.ENDDATE >= mt.start_date
      AND b.ENDDATE <= p.anchor_day
      AND b.BENCHGRFORSINCE IS NOT NULL
      AND b.INSERTTIME < DATE_ADD(p.anchor_day, INTERVAL 1 DAY)
      AND b.UPDATATIME < DATE_ADD(p.anchor_day, INTERVAL 1 DAY)
),
common_points AS (
    SELECT
        nd.INNERCODE,
        nd.trade_date,
        nd.UNITNVRESTORED,
        bd.BENCHGRFORSINCE
    FROM nav_dedup nd
    JOIN bench_dedup bd
      ON bd.INNERCODE = nd.INNERCODE
     AND bd.trade_date = nd.trade_date
    WHERE nd.rn = 1
      AND bd.rn = 1
),
common_window AS (
    SELECT
        cp.INNERCODE,
        cp.trade_date,
        cp.UNITNVRESTORED,
        cp.BENCHGRFORSINCE,
        ROW_NUMBER() OVER (
            PARTITION BY cp.INNERCODE
            ORDER BY cp.trade_date ASC
        ) AS asc_rn,
        ROW_NUMBER() OVER (
            PARTITION BY cp.INNERCODE
            ORDER BY cp.trade_date DESC
        ) AS desc_rn
    FROM common_points cp
)
SELECT
    DATE_FORMAT(mt.start_date, '%Y-%m-%d') AS `开始管理日期`,
    DATE_FORMAT(MAX(CASE WHEN cw.desc_rn = 1 THEN cw.trade_date END), '%Y-%m-%d') AS `收益率截止日期`,
    ROUND(
        MAX(CASE WHEN cw.desc_rn = 1 THEN cw.UNITNVRESTORED END)
        / NULLIF(MAX(CASE WHEN cw.asc_rn = 1 THEN cw.UNITNVRESTORED END), 0) - 1,
        4
    ) AS `任职以来基金收益`,
    ROUND(
        (1 + MAX(CASE WHEN cw.desc_rn = 1 THEN cw.BENCHGRFORSINCE END) / 100)
        / NULLIF(1 + MAX(CASE WHEN cw.asc_rn = 1 THEN cw.BENCHGRFORSINCE END) / 100, 0) - 1,
        4
    ) AS `同期基准收益`,
    ROUND(
        (
            MAX(CASE WHEN cw.desc_rn = 1 THEN cw.UNITNVRESTORED END)
            / NULLIF(MAX(CASE WHEN cw.asc_rn = 1 THEN cw.UNITNVRESTORED END), 0) - 1
        )
        -
        (
            (1 + MAX(CASE WHEN cw.desc_rn = 1 THEN cw.BENCHGRFORSINCE END) / 100)
            / NULLIF(1 + MAX(CASE WHEN cw.asc_rn = 1 THEN cw.BENCHGRFORSINCE END) / 100, 0) - 1
        ),
        4
    ) AS `超额收益`
FROM manager_target mt
JOIN common_window cw
  ON cw.INNERCODE = mt.INNERCODE
GROUP BY mt.start_date;
