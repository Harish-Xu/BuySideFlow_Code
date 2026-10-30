WITH params AS (
    SELECT DATE('2025-08-01') AS as_of_date
),
target_fund AS (
    SELECT
        fa.INNERCODE,
        fa.SECUCODE
    FROM mf_fundarchives fa
    WHERE fa.SECUCODE = '005739'
),
holder_raw AS (
    SELECT
        hi.ENDDATE,
        hi.HOLDERACCOUNTNUMBER,
        hi.INSTITUTIONHOLDRATIO,
        hi.INDIVIDUALHOLDRATIO,
        hi.PROFESSIONALHOLDRATIO,
        ROW_NUMBER() OVER (
            PARTITION BY hi.INNERCODE, hi.ENDDATE
            ORDER BY hi.INFOPUBLDATE DESC, hi.INSERTTIME DESC, hi.ID DESC
        ) AS rn
    FROM mf_holderinfo hi
    JOIN target_fund tf
      ON tf.INNERCODE = hi.INNERCODE
    CROSS JOIN params p
    WHERE hi.ENDDATE <= p.as_of_date
      AND COALESCE(hi.INFOPUBLDATE, hi.INSERTTIME, hi.ENDDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND COALESCE(hi.INSERTTIME, hi.INFOPUBLDATE, hi.ENDDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
)
SELECT
    DATE_FORMAT(hr.ENDDATE, '%Y-%m-%d') AS `报告期`,
    tf.SECUCODE AS `基金代码`,
    sm.SECUABBR AS `基金简称`,
    hr.HOLDERACCOUNTNUMBER AS `持有人户数`,
    ROUND(hr.INSTITUTIONHOLDRATIO, 2) AS `机构持有比例`,
    ROUND(hr.INDIVIDUALHOLDRATIO, 2) AS `个人持有比例`,
    ROUND(hr.PROFESSIONALHOLDRATIO, 4) AS `基金从业人员持有比例`
FROM holder_raw hr
JOIN target_fund tf
  ON 1 = 1
JOIN secumain sm
  ON sm.INNERCODE = tf.INNERCODE
WHERE hr.rn = 1
ORDER BY hr.ENDDATE;
