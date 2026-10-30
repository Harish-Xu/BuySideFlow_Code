-- Amounts in bond_issuenew are millions of yuan. One row is one issuance,
-- not one listing code: retain separate initial/reopening issuance events.
WITH RECURSIVE week_calendar AS (
    SELECT DATE('2023-12-04') AS week_start_date
    UNION ALL
    SELECT DATE_ADD(week_start_date, INTERVAL 7 DAY)
    FROM week_calendar
    WHERE week_start_date < DATE('2024-12-23')
),
issue_raw AS (
    SELECT i.MAINCODE, i.ISSUETYPE, i.PUBLICANNOUNCEMENTDATE,
           i.ISSUEDATESTART, i.ACTUALISSUESIZE
    FROM bond_issuenew i
    WHERE i.ISSUEDATESTART >= DATE('2023-12-04')
      AND i.ISSUEDATESTART < DATE('2024-12-30')
      AND i.PUBLICANNOUNCEMENTDATE < DATE('2025-01-01')
      AND i.CURRENCY = 1420
      AND i.ACTUALISSUESIZE IS NOT NULL
),
code_ranked AS (
    SELECT c.MAINCODE, c.BONDTYPELEVEL1,
           ROW_NUMBER() OVER (PARTITION BY c.MAINCODE ORDER BY c.INNERCODE) AS rn
    FROM bond_code c
    WHERE c.BONDTYPELEVEL1 IS NOT NULL
      AND c.BONDTYPELEVEL1 NOT IN (2200,2300)
      AND c.CURRENCY = 1420
      AND c.SECUMARKET IN (16,18,71,73,81,83,84,89,90,310)
      AND EXISTS (SELECT 1 FROM issue_raw i WHERE i.MAINCODE=c.MAINCODE)
),
issue_base AS (
    SELECT DATE_SUB(DATE(i.ISSUEDATESTART), INTERVAL WEEKDAY(i.ISSUEDATESTART) DAY)
               AS week_start_date,
           c.BONDTYPELEVEL1, i.ACTUALISSUESIZE
    FROM issue_raw i
    JOIN code_ranked c ON c.MAINCODE=i.MAINCODE AND c.rn=1
),
issue_types AS (
    SELECT DISTINCT BONDTYPELEVEL1 FROM issue_base
    WHERE week_start_date >= DATE('2024-01-01')
),
weekly_issue AS (
    SELECT week_start_date,BONDTYPELEVEL1,SUM(ACTUALISSUESIZE) AS weekly_issue_size
    FROM issue_base GROUP BY week_start_date,BONDTYPELEVEL1
),
panel AS (
    SELECT w.week_start_date,t.BONDTYPELEVEL1,
           COALESCE(i.weekly_issue_size,0) AS weekly_issue_size
    FROM week_calendar w CROSS JOIN issue_types t
    LEFT JOIN weekly_issue i ON i.week_start_date=w.week_start_date
                           AND i.BONDTYPELEVEL1=t.BONDTYPELEVEL1
),
scored AS (
    SELECT *,
        AVG(weekly_issue_size) OVER (
            PARTITION BY BONDTYPELEVEL1 ORDER BY week_start_date
            ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING) AS prev4_avg,
        COUNT(*) OVER (
            PARTITION BY BONDTYPELEVEL1 ORDER BY week_start_date
            ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING) AS prev4_count
    FROM panel
),
ranked AS (
    SELECT *,weekly_issue_size/prev4_avg-1 AS deviation_ratio,
        ROW_NUMBER() OVER (ORDER BY weekly_issue_size/prev4_avg DESC,
                         week_start_date,BONDTYPELEVEL1) AS up_rn
    FROM scored
    WHERE week_start_date >= DATE('2024-01-01') AND prev4_count=4 AND prev4_avg>0
)
SELECT week_start_date AS `周起始日`,BONDTYPELEVEL1 AS `债券品种代码`,
       weekly_issue_size/100 AS `当周发行规模(亿元)`,prev4_avg/100 AS `前4周平均发行规模(亿元)`,
       deviation_ratio*100 AS `增幅`
FROM ranked WHERE up_rn=1;
