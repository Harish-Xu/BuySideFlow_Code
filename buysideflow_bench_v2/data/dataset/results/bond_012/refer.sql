WITH params AS (
    SELECT
        DATE('2025-09-22') AS start_date,
        DATE('2025-10-13') AS end_date_exclusive,
        DATE('2025-10-16') AS as_of_exclusive
),
issue_stats AS (
    SELECT
        COUNT(DISTINCT i.MAINCODE) AS issue_bond_count,
        ROUND(SUM(i.PLANISSUESIZE) / 100, 2) AS planned_issue_size_100million
    FROM bond_issuenew i
    JOIN params p
      ON i.PUBLICANNOUNCEMENTDATE >= p.start_date
     AND i.PUBLICANNOUNCEMENTDATE < p.end_date_exclusive
),
net_finance AS (
    SELECT
        ROUND(
            SUM(
                CASE
                    WHEN s.ALTERATIONTYPE = 10 THEN COALESCE(s.TOTALSIZE, 0)
                    WHEN s.ALTERATIONTYPE = 60 THEN COALESCE(s.CURRENTSIZECHANGE, 0)
                    ELSE 0
                END
            ) / 100,
            2
        ) AS net_financing_100million
    FROM bond_size s
    JOIN params p
      ON s.ENDDATE >= p.start_date
     AND s.ENDDATE < p.end_date_exclusive
     AND s.INFOPUBLDATE < p.as_of_exclusive
    WHERE s.ALTERATIONTYPE IN (10, 60)
)
SELECT
    i.issue_bond_count,
    i.planned_issue_size_100million,
    COALESCE(n.net_financing_100million, 0) AS net_financing_100million
FROM issue_stats i
CROSS JOIN net_finance n;
