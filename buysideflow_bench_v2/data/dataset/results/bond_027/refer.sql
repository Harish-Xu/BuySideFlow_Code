WITH weekly AS (
    SELECT
        DATE_SUB(DATE(ENDDATE), INTERVAL WEEKDAY(ENDDATE) DAY) AS week_start_date,
        AVG(FDR007 - FR007) AS avg_gap,
        MAX(FDR007 - FR007) AS max_gap,
        MIN(FDR007 - FR007) AS min_gap
    FROM bond_baseratereference
    WHERE ENDDATE BETWEEN '2024-01-01' AND '2024-12-31'
      AND FDR007 IS NOT NULL
      AND FR007 IS NOT NULL
    GROUP BY DATE_SUB(DATE(ENDDATE), INTERVAL WEEKDAY(ENDDATE) DAY)
),
scored AS (
    SELECT
        *,
        PERCENT_RANK() OVER (ORDER BY ABS(avg_gap)) AS abs_rank
    FROM weekly
)
SELECT
    week_start_date,
    ROUND(avg_gap, 6) AS avg_gap,
    ROUND(max_gap, 6) AS max_gap,
    ROUND(min_gap, 6) AS min_gap,
    CASE WHEN abs_rank >= 0.95 THEN 1 ELSE 0 END AS is_abnormal_week
FROM scored
ORDER BY ABS(avg_gap) DESC, week_start_date;
