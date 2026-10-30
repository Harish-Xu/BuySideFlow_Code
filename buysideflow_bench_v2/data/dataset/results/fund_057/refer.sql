WITH params AS (
    SELECT
        DATE('2025-12-13') AS observation_date,
        DATE('2025-12-14') AS visible_cutoff
),
manager_positions AS (
    SELECT
        m.INNERCODE,
        m.PERSONALCODE,
        m.ACCESSIONDATE,
        ROW_NUMBER() OVER (
            PARTITION BY m.INNERCODE, m.PERSONALCODE, m.ACCESSIONDATE
            ORDER BY m.INFOPUBLDATE DESC, m.ID DESC
        ) AS rn
    FROM mf_fundmanagernew m
    JOIN params p
      ON m.INFOPUBLDATE < p.visible_cutoff
    WHERE m.POSTNAME = 1
      AND m.ACCESSIONDATE <= p.observation_date
      AND (m.DIMISSIONDATE IS NULL OR m.DIMISSIONDATE > p.observation_date)
),
managed_main_funds AS (
    SELECT DISTINCT
        mp.PERSONALCODE,
        COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) AS main_fund_code
    FROM manager_positions mp
    JOIN mf_fundarchives fa
      ON fa.INNERCODE = mp.INNERCODE
    JOIN params p
      ON fa.INSERTTIME IS NULL OR fa.INSERTTIME < p.visible_cutoff
    WHERE mp.rn = 1
),
person_info_ranked AS (
    SELECT
        pi.PERSONALCODE,
        pi.CHINESENAME,
        pi.PRACTICEDATE,
        ROW_NUMBER() OVER (
            PARTITION BY pi.PERSONALCODE
            ORDER BY pi.INSERTTIME DESC, pi.ID DESC
        ) AS rn
    FROM mf_personalinfo pi
    JOIN params p
      ON pi.INSERTTIME < p.visible_cutoff
    WHERE pi.PRACTICEDATE IS NOT NULL
),
manager_summary AS (
    SELECT
        mmf.PERSONALCODE,
        pi.CHINESENAME AS manager_name,
        TIMESTAMPDIFF(DAY, pi.PRACTICEDATE, p.observation_date) / 365.25 AS practice_years,
        COUNT(DISTINCT mmf.main_fund_code) AS current_fund_count
    FROM managed_main_funds mmf
    JOIN person_info_ranked pi
      ON pi.PERSONALCODE = mmf.PERSONALCODE
     AND pi.rn = 1
    JOIN params p
      ON pi.PRACTICEDATE <= p.observation_date
    GROUP BY mmf.PERSONALCODE, pi.CHINESENAME, pi.PRACTICEDATE, p.observation_date
)
SELECT
    PERSONALCODE AS manager_code,
    manager_name,
    ROUND(practice_years, 4) AS practice_years,
    current_fund_count
FROM manager_summary
WHERE practice_years >= 30
ORDER BY current_fund_count DESC, practice_years DESC, manager_name;
