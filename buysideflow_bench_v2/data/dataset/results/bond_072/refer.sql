WITH put_events AS (
    -- bond_size is unique by (MAINCODE, ENDDATE); listings must not multiply events.
    SELECT
        s.MAINCODE,
        s.ENDDATE AS change_date,
        -s.CURRENTSIZECHANGE AS decrease_mn
    FROM bond_size s
    WHERE s.ENDDATE >= '2024-12-01'
      AND s.ENDDATE < '2025-01-01'
      AND s.INFOPUBLDATE < '2025-01-04'
      AND s.ALTERATIONTYPE = 23
      AND s.CURRENTSIZECHANGE <= -500
      AND EXISTS (
          SELECT 1
          FROM bond_code bc
          WHERE bc.MAINCODE = s.MAINCODE
            AND (
                bc.BONDTYPELEVEL1 IN (1600,1700,1800,2500,2600,2800,2900,3100,3200,3300,3700)
                OR bc.BONDTYPELEVEL2 IN (1901,1903)
            )
      )
),
previous_sizes AS (
    SELECT
        e.MAINCODE,
        e.change_date,
        e.decrease_mn,
        s.TOTALSIZE AS pre_size_mn,
        ROW_NUMBER() OVER (
            PARTITION BY e.MAINCODE, e.change_date
            ORDER BY s.ENDDATE DESC
        ) AS rn
    FROM put_events e
    JOIN bond_size s
      ON s.MAINCODE = e.MAINCODE
     AND s.ENDDATE < e.change_date
     AND s.INFOPUBLDATE < '2025-01-04'
),
qualified_events AS (
    -- Rank before testing size, so an older positive balance cannot replace the latest period.
    SELECT MAINCODE, change_date, decrease_mn, pre_size_mn
    FROM previous_sizes
    WHERE rn = 1
      AND pre_size_mn > 0
      AND decrease_mn >= pre_size_mn * 0.70
)
SELECT
    COUNT(*) AS `事件数量`,
    ROUND(COALESCE(SUM(decrease_mn), 0) / 100.0, 4) AS `规模减少额合计(亿元)`
FROM qualified_events;
