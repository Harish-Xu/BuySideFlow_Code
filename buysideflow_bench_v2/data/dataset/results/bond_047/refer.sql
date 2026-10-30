-- Base extraction used by refer.py.
-- It first limits to city-investment bonds already outstanding in the six
-- target regions on 2024-12-31, then takes the latest bond_size.TOTALSIZE
-- record known by the observation date. First put-date parsing is done in Python as
-- VALUEDATE + the first OPMATURITY segment because OPMATURITY is a terms
-- string such as "3+2"; issuer-level aggregation is also done in Python.
WITH bond_start_dates AS (
    SELECT
        bc.MAINCODE AS main_code,
        MIN(
            LEAST(
                COALESCE(bc.LISTEDDATE, '9999-12-31'),
                COALESCE(bi.LISTEDDATE, '9999-12-31'),
                COALESCE(bi.VALUEDATE, '9999-12-31'),
                COALESCE(bi.ISSUEDATE, '9999-12-31'),
                COALESCE(bi.INITIALINFOPUBLDATE, '9999-12-31')
            )
        ) AS start_date
    FROM bond_code bc
    LEFT JOIN bond_basicinfo bi
      ON bi.INNERCODE = bc.INNERCODE
    WHERE bc.MAINCODE IS NOT NULL
    GROUP BY bc.MAINCODE
), live_city AS (
    SELECT
        bc.MAINCODE AS main_code,
        MIN(bc.COMPANYCODE) AS company_code,
        MIN(bc.ISSUER) AS issuer,
        MIN(
            CASE
                WHEN ia.REGCITY >= 120000 AND ia.REGCITY < 130000 THEN '天津'
                WHEN ia.REGCITY >= 450000 AND ia.REGCITY < 460000 THEN '广西'
                WHEN ia.REGCITY >= 510000 AND ia.REGCITY < 520000 THEN '四川'
                WHEN ia.REGCITY >= 520000 AND ia.REGCITY < 530000 THEN '贵州'
                WHEN ia.REGCITY >= 530000 AND ia.REGCITY < 540000 THEN '云南'
                WHEN ia.REGCITY >= 620000 AND ia.REGCITY < 630000 THEN '甘肃'
                WHEN ia.REGADDR LIKE '天津%' THEN '天津'
                WHEN ia.REGADDR LIKE '广西%' THEN '广西'
                WHEN ia.REGADDR LIKE '四川%' THEN '四川'
                WHEN ia.REGADDR LIKE '贵州%' THEN '贵州'
                WHEN ia.REGADDR LIKE '云南%' THEN '云南'
                WHEN ia.REGADDR LIKE '甘肃%' THEN '甘肃'
            END
        ) AS issuer_area,
        MAX(bc.INTERESTENDDATE) AS maturity_date
    FROM bond_code bc
    JOIN lc_instiarchive ia
      ON ia.COMPANYCODE = bc.COMPANYCODE
    JOIN bond_conceptnature cn
      ON cn.INNERCODE = bc.INNERCODE
    JOIN bond_start_dates bsd
      ON bsd.main_code = bc.MAINCODE
    WHERE bc.COMPANYCODE IS NOT NULL
      AND bc.MAINCODE IS NOT NULL
      AND bc.INTERESTENDDATE >= '2025-01-01'
      AND (bc.DELISTDATE IS NULL OR bc.DELISTDATE >= '2025-01-01')
      AND bsd.start_date < '2025-01-01'
      AND (ia.STARTDATE IS NULL OR ia.STARTDATE < '2025-01-01')
      AND (ia.CLOSEDATE IS NULL OR ia.CLOSEDATE > '2024-12-31')
      AND cn.CONCEPTTYPE = 40
      AND cn.CONNATURE = 4041
      AND (cn.BEGINDATE IS NULL OR cn.BEGINDATE < '2025-01-01')
      AND (cn.ENDDATE IS NULL OR cn.ENDDATE > '2024-12-31')
      AND cn.INSERTTIME < '2025-01-01'
      AND (
            (ia.REGCITY >= 120000 AND ia.REGCITY < 130000)
         OR (ia.REGCITY >= 450000 AND ia.REGCITY < 460000)
         OR (ia.REGCITY >= 510000 AND ia.REGCITY < 520000)
         OR (ia.REGCITY >= 520000 AND ia.REGCITY < 530000)
         OR (ia.REGCITY >= 530000 AND ia.REGCITY < 540000)
         OR (ia.REGCITY >= 620000 AND ia.REGCITY < 630000)
         OR ia.REGADDR LIKE '天津%'
         OR ia.REGADDR LIKE '广西%'
         OR ia.REGADDR LIKE '四川%'
         OR ia.REGADDR LIKE '贵州%'
         OR ia.REGADDR LIKE '云南%'
         OR ia.REGADDR LIKE '甘肃%'
      )
    GROUP BY bc.MAINCODE
), latest_size_date AS (
    SELECT
        s.MAINCODE AS main_code,
        MAX(s.ENDDATE) AS max_enddate
    FROM bond_size s
    JOIN live_city lc
      ON lc.main_code = s.MAINCODE
    WHERE s.ENDDATE <= '2024-12-31'
      AND (s.INFOPUBLDATE IS NULL OR s.INFOPUBLDATE < '2025-01-01')
      AND s.UPDATETIME < '2025-01-01'
      AND s.TOTALSIZE IS NOT NULL
      AND s.TOTALSIZE > 0
    GROUP BY s.MAINCODE
)
SELECT
    lc.main_code,
    lc.company_code,
    lc.issuer,
    lc.issuer_area,
    lc.maturity_date,
    s.TOTALSIZE AS current_size_million
FROM live_city lc
JOIN latest_size_date lsd
  ON lsd.main_code = lc.main_code
JOIN bond_size s
  ON s.MAINCODE = lsd.main_code
 AND s.ENDDATE = lsd.max_enddate
WHERE s.TOTALSIZE IS NOT NULL
  AND s.TOTALSIZE > 0
  AND s.UPDATETIME < '2025-01-01';
