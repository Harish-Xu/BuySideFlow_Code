-- Monitor departure announcements, not departures effective during this period.
-- A disclosed future departure is eligible; an undisclosed departure is not.
WITH params AS (
    SELECT DATE('2026-01-01') AS monitor_start,
           DATE('2026-02-28') AS as_of_date
),
tracked_funds AS (
    SELECT
        sm.INNERCODE,
        sm.SECUCODE,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, sm.SECUCODE) AS fund_name
    FROM secumain sm
    WHERE sm.SECUCATEGORY = 8
      AND sm.SECUCODE IN ('000171', '159321', '420102', '001570', '005943')
),
manager_dimissions AS (
    SELECT
        tf.INNERCODE,
        tf.SECUCODE,
        tf.fund_name,
        m.PERSONALCODE,
        m.NAME,
        m.ACCESSIONDATE,
        m.DIMISSIONDATE,
        m.INFOPUBLDATE,
        ROW_NUMBER() OVER (
            PARTITION BY tf.INNERCODE, m.PERSONALCODE, m.ACCESSIONDATE, COALESCE(m.DIMISSIONDATE, DATE('9999-12-31'))
            ORDER BY COALESCE(m.INFOPUBLDATE, DATE('1900-01-01')) DESC, m.ID DESC
        ) AS rn
    FROM mf_fundmanagernew m
    JOIN tracked_funds tf
      ON tf.INNERCODE = m.INNERCODE
    JOIN params p
      ON 1 = 1
    WHERE m.POSTNAME = 1
      AND m.DIMISSIONDATE IS NOT NULL
      -- Include all of 2026-02-28, even when the publication has a time component.
      AND m.INFOPUBLDATE >= p.monitor_start
      AND m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
)
SELECT
    CONCAT(me.SECUCODE, '.OF') AS `基金代码`,
    me.fund_name AS `基金简称`,
    me.NAME AS `基金经理姓名`,
    DATE_FORMAT(me.DIMISSIONDATE, '%Y-%m-%d') AS `离任日期`,
    DATE_FORMAT(me.INFOPUBLDATE, '%Y-%m-%d') AS `公告日期`
FROM manager_dimissions me
WHERE me.rn = 1
ORDER BY me.INFOPUBLDATE ASC, me.SECUCODE ASC;
