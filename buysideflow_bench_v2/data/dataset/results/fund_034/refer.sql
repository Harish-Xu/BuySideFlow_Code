WITH params AS (
    SELECT DATE('2025-07-04') AS as_of_date
),
target_fund AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        sm.SECUABBR AS fund_name
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    WHERE fa.SECURITYCODE = '011921'
    ORDER BY fa.INNERCODE
    LIMIT 1
),
scale_ranked AS (
    SELECT
        s.INNERCODE,
        DATE(s.ENDDATE) AS report_date,
        s.NVII AS actual_scale,
        ROW_NUMBER() OVER (
            PARTITION BY s.INNERCODE, DATE(s.ENDDATE)
            ORDER BY s.INSERTTIME DESC, s.UPDATETIME DESC, s.ID DESC
        ) AS rn
    FROM mf_scaleanalysis s
    JOIN target_fund tf
      ON tf.INNERCODE = s.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE s.ENDDATE <= p.as_of_date
      AND s.NVII IS NOT NULL
      AND s.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND s.UPDATETIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
),
scale_series AS (
    SELECT
        INNERCODE,
        report_date,
        actual_scale
    FROM scale_ranked
    WHERE rn = 1
),
ordered_scale AS (
    SELECT
        ss.*,
        LAG(ss.actual_scale) OVER (ORDER BY ss.report_date) AS previous_scale,
        ROW_NUMBER() OVER (ORDER BY ss.report_date DESC) AS rn_desc
    FROM scale_series ss
),
summary AS (
    SELECT
        MAX(CASE WHEN rn_desc = 1 THEN actual_scale END) AS latest_scale,
        MAX(CASE WHEN rn_desc = 1 THEN previous_scale END) AS previous_scale,
        MAX(actual_scale) AS max_scale
    FROM ordered_scale
)
SELECT
    DATE_FORMAT(os.report_date, '%Y-%m-%d') AS `报告期`,
    ROUND(os.actual_scale / 100000000, 2) AS `实际规模(亿元)`,
    ROUND(sm.latest_scale / 100000000, 2) AS `最新可得规模(亿元)`,
    ROUND(sm.max_scale / 100000000, 2) AS `历史最大规模(亿元)`,
    ROUND((sm.latest_scale - sm.previous_scale) / NULLIF(sm.previous_scale, 0) * 100, 2) AS `最新规模较上一期变化幅度(%)`,
    ROUND((sm.latest_scale - sm.max_scale) / NULLIF(sm.max_scale, 0) * 100, 2) AS `最新规模较历史最大变化幅度(%)`
FROM ordered_scale os
JOIN target_fund tf
  ON tf.INNERCODE = os.INNERCODE
CROSS JOIN summary sm
ORDER BY os.report_date;
