WITH params AS (
    SELECT DATE('2025-02-28') AS observation_date
),
type_asof AS (
    SELECT innercode, secassetcatcode
    FROM (
        SELECT
            jt.innercode,
            jt.secassetcatcode,
            ROW_NUMBER() OVER (
                PARTITION BY jt.innercode
                ORDER BY jt.effectivedate DESC, jt.inserttime DESC, jt.id DESC
            ) AS rn
        FROM mf_jyfundtype jt
        JOIN params p
          ON jt.effectivedate <= p.observation_date
         AND (jt.canceldate IS NULL OR jt.canceldate > p.observation_date)
        WHERE jt.standard = 75
          AND jt.inserttime < DATE_ADD(p.observation_date, INTERVAL 1 DAY)
    ) t
    WHERE rn = 1
),
fund_pool AS (
    SELECT DISTINCT
        fa.innercode,
        fa.secucode AS fund_code
    FROM mf_fundarchives fa
    JOIN type_asof ta
      ON ta.innercode = fa.innercode
    JOIN params p
      ON 1 = 1
    WHERE fa.type IN (2, 3, 4, 8)
      AND (fa.expiredate IS NULL OR DATE(fa.expiredate) > p.observation_date)
      AND fa.maincode = fa.secucode
      AND ta.secassetcatcode = 1201
),
available_quarters AS (
    SELECT end_date
    FROM (
        SELECT DISTINCT DATE(ip.enddate) AS end_date
        FROM mf_industryportall ip
        JOIN fund_pool fp
          ON fp.innercode = ip.innercode
        JOIN params p
          ON 1 = 1
        WHERE ip.reporttype IN (17, 61, 23, 63)
          AND ip.investtype = 1
          AND ip.industandard = 22
          AND ip.ratioinnv IS NOT NULL
          AND DATE(ip.enddate) <= p.observation_date
          AND (ip.infopubldate IS NULL OR ip.infopubldate < DATE_ADD(p.observation_date, INTERVAL 1 DAY))
          AND ip.inserttime < DATE_ADD(p.observation_date, INTERVAL 1 DAY)
        ORDER BY end_date DESC
        LIMIT 4
    ) q
),
ordered_quarters AS (
    SELECT
        end_date,
        ROW_NUMBER() OVER (ORDER BY end_date) AS q_rank
    FROM available_quarters
),
base_raw AS (
    SELECT
        ip.innercode,
        DATE(ip.enddate) AS end_date,
        ip.industrycode,
        ip.ratioinnv,
        ROW_NUMBER() OVER (
            PARTITION BY ip.innercode, DATE(ip.enddate), ip.industrycode
            ORDER BY ip.infopubldate DESC, ip.inserttime DESC, ip.id DESC
        ) AS rn
    FROM mf_industryportall ip
    JOIN fund_pool fp
      ON fp.innercode = ip.innercode
    JOIN ordered_quarters oq
      ON oq.end_date = DATE(ip.enddate)
    JOIN params p
      ON 1 = 1
    WHERE ip.reporttype IN (17, 61, 23, 63)
      AND ip.investtype = 1
      AND ip.industandard = 22
      AND ip.ratioinnv IS NOT NULL
      AND (ip.infopubldate IS NULL OR ip.infopubldate < DATE_ADD(p.observation_date, INTERVAL 1 DAY))
      AND ip.inserttime < DATE_ADD(p.observation_date, INTERVAL 1 DAY)
),
base AS (
    SELECT
        innercode,
        end_date,
        industrycode,
        ratioinnv AS weight
    FROM base_raw
    WHERE rn = 1
),
complete_funds AS (
    SELECT
        b.innercode,
        GROUP_CONCAT(DATE_FORMAT(b.end_date, '%Y-%m-%d') ORDER BY b.end_date SEPARATOR ',') AS report_periods
    FROM (
        SELECT DISTINCT innercode, end_date
        FROM base
    ) b
    GROUP BY b.innercode
    HAVING COUNT(*) = 4
),
pairs AS (
    SELECT
        q1.end_date AS dt1,
        q2.end_date AS dt2
    FROM ordered_quarters q1
    JOIN ordered_quarters q2
      ON q2.q_rank = q1.q_rank + 1
),
pair_union AS (
    SELECT
        p.dt1,
        p.dt2,
        cf.innercode,
        b1.industrycode,
        b1.weight AS w1,
        CAST(NULL AS DECIMAL(20,8)) AS w2
    FROM pairs p
    JOIN complete_funds cf
      ON 1 = 1
    JOIN base b1
      ON b1.innercode = cf.innercode
     AND b1.end_date = p.dt1

    UNION ALL

    SELECT
        p.dt1,
        p.dt2,
        cf.innercode,
        b2.industrycode,
        CAST(NULL AS DECIMAL(20,8)) AS w1,
        b2.weight AS w2
    FROM pairs p
    JOIN complete_funds cf
      ON 1 = 1
    JOIN base b2
      ON b2.innercode = cf.innercode
     AND b2.end_date = p.dt2
),
pair_aligned AS (
    SELECT
        innercode,
        dt1,
        dt2,
        industrycode,
        COALESCE(MAX(w1), 0) AS w1,
        COALESCE(MAX(w2), 0) AS w2
    FROM pair_union
    GROUP BY innercode, dt1, dt2, industrycode
),
drift_per_pair AS (
    SELECT
        innercode,
        dt1,
        dt2,
        SQRT(SUM(POWER(w2 - w1, 2))) AS pair_drift
    FROM pair_aligned
    GROUP BY innercode, dt1, dt2
),
drift_total AS (
    SELECT
        d.innercode,
        cf.report_periods,
        SUM(d.pair_drift) AS total_drift,
        AVG(d.pair_drift) AS avg_drift,
        COUNT(*) AS comparable_segment_count
    FROM drift_per_pair d
    JOIN complete_funds cf
      ON cf.innercode = d.innercode
    GROUP BY d.innercode, cf.report_periods
    HAVING COUNT(*) = 3
)
SELECT
    p.observation_date AS `观察日`,
    fp.fund_code AS `基金代码`,
    COALESCE(sm.chinameabbr, sm.secuabbr, fp.fund_code) AS `基金简称`,
    dt.report_periods AS `最近四个报告期`,
    ROUND(dt.total_drift, 4) AS `累计漂移`,
    ROUND(dt.avg_drift, 4) AS `平均漂移`,
    dt.comparable_segment_count AS `可比段数`
FROM drift_total dt
JOIN fund_pool fp
  ON fp.innercode = dt.innercode
LEFT JOIN secumain sm
  ON sm.innercode = fp.innercode
CROSS JOIN params p
ORDER BY dt.total_drift DESC, fp.fund_code ASC
LIMIT 20;
