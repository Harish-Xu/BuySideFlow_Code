WITH params AS (
    SELECT DATE('2025-09-05') AS as_of_date
),
social_financing_raw AS (
    SELECT
        DATE(m.ENDDATE) AS month_end_date,
        DATE_FORMAT(m.ENDDATE, '%Y-%m') AS month_label,
        YEAR(m.ENDDATE) * 12 + MONTH(m.ENDDATE) AS month_index,
        m.DATAVALUE AS social_financing_yoy,
        ROW_NUMBER() OVER (
            PARTITION BY YEAR(m.ENDDATE), MONTH(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC, m.ID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    JOIN params p
      ON DATE(m.ENDDATE) <= p.as_of_date
     AND m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE m.INDICATORCODE = 150006354 -- 社会融资规模存量:同比:月
      AND m.ENDDATE >= DATE('2021-01-01')
),
monthly_values AS (
    SELECT
        month_end_date,
        month_label,
        month_index,
        social_financing_yoy
    FROM social_financing_raw
    WHERE rn = 1 AND social_financing_yoy IS NOT NULL
),
credit_pulse AS (
    SELECT
        cur.month_end_date,
        cur.month_label,
        cur.month_index,
        cur.social_financing_yoy,
        prev.social_financing_yoy AS social_financing_yoy_12m_ago,
        cur.social_financing_yoy - prev.social_financing_yoy AS credit_pulse
    FROM monthly_values cur
    JOIN monthly_values prev
      ON prev.month_index = cur.month_index - 12
    WHERE cur.month_end_date >= DATE('2022-01-01')
),
ranked_pulse AS (
    SELECT
        month_end_date,
        month_label,
        month_index,
        credit_pulse,
        CUME_DIST() OVER (ORDER BY credit_pulse) AS historical_percentile
    FROM credit_pulse
),
labeled_pulse AS (
    SELECT
        *,
        CASE
            WHEN historical_percentile >= 0.8 THEN '信用扩张'
            WHEN historical_percentile <= 0.2 THEN '信用收缩'
            ELSE '中性'
        END AS status_label
    FROM ranked_pulse
),
status_changes AS (
    SELECT
        *,
        CASE
            WHEN LAG(status_label) OVER (ORDER BY month_end_date) IS NULL THEN 1
            WHEN status_label <> LAG(status_label) OVER (ORDER BY month_end_date) THEN 1
            WHEN month_index <> LAG(month_index) OVER (ORDER BY month_end_date) + 1 THEN 1
            ELSE 0
        END AS is_new_group
    FROM labeled_pulse
),
status_grouped AS (
    SELECT
        *,
        SUM(is_new_group) OVER (ORDER BY month_end_date) AS group_id
    FROM status_changes
),
status_intervals AS (
    SELECT
        MIN(month_end_date) AS start_date,
        MAX(month_end_date) AS end_date,
        COUNT(*) AS duration_months,
        status_label,
        ROUND(AVG(credit_pulse), 3) AS avg_credit_pulse
    FROM status_grouped
    WHERE status_label IN ('信用扩张', '信用收缩')
    GROUP BY status_label, group_id
)
SELECT
    DATE_FORMAT(start_date, '%Y-%m') AS `开始月份`,
    DATE_FORMAT(end_date, '%Y-%m') AS `结束月份`,
    duration_months AS `持续月数`,
    status_label AS `状态`,
    avg_credit_pulse AS `区间信用脉冲均值`
FROM status_intervals
ORDER BY start_date;
