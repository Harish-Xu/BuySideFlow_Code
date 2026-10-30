WITH params AS (
    SELECT DATE('2025-08-29') AS as_of_date
),
target_fund AS (
    SELECT sm.INNERCODE
    FROM secumain sm
    WHERE sm.SECUCODE = '000171'
      AND sm.SECUCATEGORY = 8
),
current_benchmark AS (
    SELECT
        ba.TRACEDINDEXCODE,
        ba.MINIMUMWEIGHT,
        ba.SERIALNUMBER
    FROM mf_benchmarkallocation ba
    JOIN target_fund tf
      ON tf.INNERCODE = ba.INNERCODE
    CROSS JOIN params p
    WHERE ba.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND ba.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND ba.EXCUTEDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND (ba.CANCELDATE IS NULL OR ba.CANCELDATE >= DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
      AND ba.TRACEDINDEXCODE IS NOT NULL
)
SELECT
    COALESCE(idx.SECUCODE, CAST(cb.TRACEDINDEXCODE AS CHAR)) AS `基准代码`,
    idx.SECUABBR AS `基准简称`,
    ROUND(cb.MINIMUMWEIGHT * 100, 2) AS `权重下限（%）`
FROM current_benchmark cb
LEFT JOIN secumain idx
  ON idx.INNERCODE = cb.TRACEDINDEXCODE
ORDER BY cb.SERIALNUMBER ASC, `基准代码` ASC;
