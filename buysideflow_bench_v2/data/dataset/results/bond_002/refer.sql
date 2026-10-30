WITH params AS (
    SELECT
        DATE('2025-06-02') AS as_of_date,
        DATE_SUB(DATE('2025-06-02'), INTERVAL DAYOFMONTH(DATE('2025-06-02')) - 1 DAY) AS asof_month_start
),
bond_monthly_raw AS (
    SELECT
        DATE_SUB(m.ENDDATE, INTERVAL DAYOFMONTH(m.ENDDATE) - 1 DAY) AS month_start,
        m.ENDDATE,
        m.DATAVALUE / 100.0 AS bond_yield,
        m.INFOPUBLDATE,
        m.JSID
    FROM c_ed_macroindicatordata m
    CROSS JOIN params p
    WHERE m.INDICATORCODE = 600020013
      AND m.DATAVALUE IS NOT NULL
      AND m.ENDDATE >= DATE_SUB(p.asof_month_start, INTERVAL 36 MONTH)
      AND m.ENDDATE < p.asof_month_start
      AND m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
),
bond_monthly AS (
    SELECT
        month_start,
        bond_yield
    FROM (
        SELECT
            month_start,
            bond_yield,
            ROW_NUMBER() OVER (
                PARTITION BY month_start
                ORDER BY ENDDATE DESC, INFOPUBLDATE DESC, JSID DESC
            ) AS rn
        FROM bond_monthly_raw
    ) t
    WHERE rn = 1
),
pe_monthly_raw AS (
    SELECT
        DATE_SUB(i.TRADINGDAY, INTERVAL DAYOFMONTH(i.TRADINGDAY) - 1 DAY) AS month_start,
        i.TRADINGDAY,
        i.PE_TTM AS pe_ratio,
        i.JSID,
        i.ID
    FROM lc_indexderivative i
    CROSS JOIN params p
    WHERE i.INDEXCODE = 3145
      AND i.PE_TTM IS NOT NULL
      AND i.PE_TTM > 0
      AND i.TRADINGDAY >= DATE_SUB(p.asof_month_start, INTERVAL 36 MONTH)
      AND i.TRADINGDAY < p.asof_month_start
      AND i.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
),
pe_monthly AS (
    SELECT
        month_start,
        pe_ratio
    FROM (
        SELECT
            month_start,
            pe_ratio,
            ROW_NUMBER() OVER (
                PARTITION BY month_start
                ORDER BY TRADINGDAY DESC, JSID DESC, ID DESC
            ) AS rn
        FROM pe_monthly_raw
    ) t
    WHERE rn = 1
),
monthly_levels AS (
    SELECT
        b.month_start,
        b.bond_yield,
        p.pe_ratio
    FROM bond_monthly b
    JOIN pe_monthly p
      ON b.month_start = p.month_start
),
complete_monthly_levels AS (
    -- The requested window contains exactly 36 distinct calendar months.
    -- Fail closed instead of letting ROWS-based windows bridge a missing month.
    SELECT *
    FROM monthly_levels
    WHERE (SELECT COUNT(*) FROM monthly_levels) = 36
),
monthly_changes AS (
    SELECT
        month_start,
        bond_yield - LAG(bond_yield) OVER (ORDER BY month_start) AS bond_yield_change,
        pe_ratio - LAG(pe_ratio) OVER (ORDER BY month_start) AS pe_change
    FROM complete_monthly_levels
),
rolling_base AS (
    SELECT
        month_start,
        bond_yield_change,
        pe_change
    FROM monthly_changes
    WHERE bond_yield_change IS NOT NULL
      AND pe_change IS NOT NULL
),
rolling_stats AS (
    SELECT
        month_start,
        COUNT(*) OVER w AS n,
        SUM(bond_yield_change) OVER w AS sum_x,
        SUM(pe_change) OVER w AS sum_y,
        SUM(bond_yield_change * pe_change) OVER w AS sum_xy,
        SUM(bond_yield_change * bond_yield_change) OVER w AS sum_x2,
        SUM(pe_change * pe_change) OVER w AS sum_y2
    FROM rolling_base
    WINDOW w AS (ORDER BY month_start ROWS BETWEEN 11 PRECEDING AND CURRENT ROW)
),
rolling_correlation AS (
    SELECT
        month_start,
        (
            (n * sum_xy - sum_x * sum_y) /
            NULLIF(
                SQRT(n * sum_x2 - POW(sum_x, 2)) * SQRT(n * sum_y2 - POW(sum_y, 2)),
                0
            )
        ) AS correlation_coefficient
    FROM rolling_stats
    WHERE n = 12
),
negative_correlation AS (
    SELECT
        month_start,
        correlation_coefficient,
        TIMESTAMPDIFF(MONTH, DATE('2000-01-01'), month_start)
        - ROW_NUMBER() OVER (ORDER BY month_start) AS group_key
    FROM rolling_correlation
    WHERE correlation_coefficient < 0
)
SELECT
    DATE_FORMAT(MIN(month_start), '%Y-%m') AS start_month,
    DATE_FORMAT(MAX(month_start), '%Y-%m') AS end_month,
    ROUND(AVG(correlation_coefficient), 3) AS avg_correlation_coefficient
FROM negative_correlation
GROUP BY group_key
ORDER BY start_month;
