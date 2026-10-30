WITH params AS (
    SELECT DATE('2025-10-27') AS observe_date
),
pmi_raw AS (
    SELECT
        DATE(m.ENDDATE) AS end_date,
        m.DATAVALUE AS pmi_value,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC, m.UPDATETIME DESC, m.ID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    JOIN c_ed_indicatormain i
      ON i.INDICATORCODE = m.INDICATORCODE
    JOIN params p
      ON m.INFOPUBLDATE < DATE_ADD(p.observe_date, INTERVAL 1 DAY)
    WHERE m.INDICATORCODE = 110166523
      AND i.DISCLOSUREFREQUENCY = 30
      AND m.DATAVALUE IS NOT NULL
),
pmi_state AS (
    SELECT
        end_date,
        pmi_value,
        CASE
            WHEN pmi_value > 50 THEN '扩张'
            WHEN pmi_value < 50 THEN '收缩'
        END AS state_label
    FROM pmi_raw
    WHERE rn = 1
),
non_neutral_months AS (
    SELECT
        end_date,
        state_label,
        TIMESTAMPDIFF(MONTH, DATE('1970-01-01'), end_date)
          - ROW_NUMBER() OVER (
              PARTITION BY state_label
              ORDER BY end_date
            ) AS grp
    FROM pmi_state
    WHERE state_label IS NOT NULL
),
state_intervals AS (
    SELECT
        state_label,
        MIN(end_date) AS start_date,
        MAX(end_date) AS end_date,
        COUNT(*) AS duration_months
    FROM non_neutral_months
    GROUP BY state_label, grp
    HAVING COUNT(*) >= 3
)
SELECT
    state_label AS `状态`,
    start_date AS `开始时间`,
    end_date AS `结束时间`,
    duration_months AS `持续月数`
FROM state_intervals
ORDER BY start_date;
