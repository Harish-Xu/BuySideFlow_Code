WITH issue_ranked AS (
    SELECT
        CASE
            WHEN BONDNATURE = 4 AND ISSUER = '中华人民共和国财政部' THEN '国债'
            WHEN BONDNATURE = 2 AND ISSUER = '国家开发银行' THEN '国开债'
        END AS bond_type,
        DATE(BIDSTARTDATE) AS bid_start_date,
        ACTUALISSUESIZE AS issue_size,
        ROW_NUMBER() OVER (
            PARTITION BY MAINCODE, BIDSTARTDATE, ISSUECODEADD
            ORDER BY UPDATETIME DESC, ID DESC
        ) AS rn
    FROM bond_issuenew
    WHERE BIDSTARTDATE >= DATE('2023-11-06')
      AND BIDSTARTDATE < DATE('2025-01-06')
      AND (
            (BONDNATURE = 4 AND ISSUER = '中华人民共和国财政部')
         OR (BONDNATURE = 2 AND ISSUER = '国家开发银行')
      )
),
weekly_issue AS (
    SELECT
        bond_type,
        DATE_SUB(bid_start_date, INTERVAL WEEKDAY(bid_start_date) DAY) AS week_start_date,
        CASE WHEN COUNT(*) = COUNT(issue_size) THEN SUM(issue_size) END AS weekly_issue_size
    FROM issue_ranked
    WHERE bond_type IS NOT NULL AND rn = 1
    GROUP BY
        bond_type,
        DATE_SUB(bid_start_date, INTERVAL WEEKDAY(bid_start_date) DAY)
),
curve_days AS (
    SELECT DISTINCT DATE(ENDDATE) AS obs_date, CURVECODE
    FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE ENDDATE >= DATE('2024-01-01') AND ENDDATE < DATE('2025-02-01')
      AND CURVECODE IN (10, 195) AND YIELDTYPECODE = 1
),
curve_ranked AS (
    SELECT
        DATE(ENDDATE) AS obs_date,
        CURVECODE,
        YIELD AS curve_yield,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(ENDDATE), CURVECODE, YEARSTOMATURITY
            ORDER BY UPDATETIME DESC, ID DESC
        ) AS rn
    FROM bond_cbyieldcurve FORCE INDEX (IX_BOND_CBYIELDCURVE)
    WHERE ENDDATE >= DATE('2024-01-01')
      AND ENDDATE < DATE('2025-02-01')
      AND CURVECODE IN (10, 195)
      AND YIELDTYPECODE = 1
      AND YEARSTOMATURITY = 5
)
SELECT
    'issue' AS row_type,
    bond_type,
    week_start_date,
    CAST(NULL AS DATE) AS obs_date,
    weekly_issue_size,
    CAST(NULL AS DECIMAL(20, 8)) AS curve_yield
FROM weekly_issue
UNION ALL
SELECT
    'curve' AS row_type,
    CASE WHEN d.CURVECODE = 10 THEN '国债' ELSE '国开债' END AS bond_type,
    CAST(NULL AS DATE) AS week_start_date,
    d.obs_date,
    CAST(NULL AS DECIMAL(20, 8)) AS weekly_issue_size,
    c.curve_yield
FROM curve_days d
LEFT JOIN curve_ranked c ON c.CURVECODE = d.CURVECODE AND c.obs_date = d.obs_date AND c.rn = 1
ORDER BY row_type, bond_type, week_start_date, obs_date;
