-- bond_064: AAA/AA+ LGFV bonds in Guizhou/Yunnan/Sichuan with 1-5Y
-- remaining maturity whose ChinaBond recommended valuation yield is in a
-- high own-history percentile as of 2025-04-30.
--
-- The script is intentionally staged with temporary tables because
-- bond_cbvaluationall is large. Each step first restricts the universe and
-- then joins the next large table.

DROP TEMPORARY TABLE IF EXISTS tmp_city_code;
CREATE TEMPORARY TABLE tmp_city_code AS
SELECT DISTINCT
    bc.MAINCODE,
    bc.INNERCODE,
    bc.SECUMARKET,
    bc.SECUCODE,
    bc.SECUABBR,
    bc.ISSUER,
    CASE
        WHEN (ia.REGCITY >= 520000 AND ia.REGCITY < 530000) OR ia.REGADDR LIKE '%贵州%' THEN '贵州'
        WHEN (ia.REGCITY >= 530000 AND ia.REGCITY < 540000) OR ia.REGADDR LIKE '%云南%' THEN '云南'
        WHEN (ia.REGCITY >= 510000 AND ia.REGCITY < 520000) OR ia.REGADDR LIKE '%四川%' THEN '四川'
    END AS REGION
FROM bond_conceptnature cn
JOIN bond_code bc
  ON cn.INNERCODE = bc.INNERCODE
JOIN lc_instiarchive ia
  ON ia.COMPANYCODE = bc.COMPANYCODE
WHERE cn.CONCEPTTYPE = 40
  AND cn.CONNATURE = 4041
  AND DATE(cn.BEGINDATE) <= DATE('2025-04-30')
  AND (cn.ENDDATE IS NULL OR DATE(cn.ENDDATE) > DATE('2025-04-30'))
  AND (cn.INSERTTIME IS NULL OR cn.INSERTTIME < DATE_ADD(DATE('2025-04-30'), INTERVAL 1 DAY))
  AND bc.MAINCODE IS NOT NULL
  AND bc.INNERCODE IS NOT NULL
  AND bc.SECUCODE IS NOT NULL
  AND bc.COMPANYCODE IS NOT NULL
  AND (
      (ia.REGCITY >= 510000 AND ia.REGCITY < 540000)
      OR ia.REGADDR LIKE '%贵州%'
      OR ia.REGADDR LIKE '%云南%'
      OR ia.REGADDR LIKE '%四川%'
  )
  AND (ia.STARTDATE IS NULL OR DATE(ia.STARTDATE) <= DATE('2025-04-30'))
  AND (ia.CLOSEDATE IS NULL OR DATE(ia.CLOSEDATE) > DATE('2025-04-30'))
  AND (bc.LISTEDDATE IS NULL OR DATE(bc.LISTEDDATE) <= DATE('2025-04-30'))
  AND (bc.INTERESTENDDATE IS NULL OR DATE(bc.INTERESTENDDATE) > DATE('2025-04-30'))
  AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) > DATE('2025-04-30'));

ALTER TABLE tmp_city_code ADD INDEX idx_city_main (MAINCODE), ADD INDEX idx_city_inner (INNERCODE);

DROP TEMPORARY TABLE IF EXISTS tmp_city_main;
CREATE TEMPORARY TABLE tmp_city_main AS
SELECT DISTINCT MAINCODE
FROM tmp_city_code;

ALTER TABLE tmp_city_main ADD PRIMARY KEY (MAINCODE);

DROP TEMPORARY TABLE IF EXISTS tmp_rating;
CREATE TEMPORARY TABLE tmp_rating AS
SELECT MAINCODE, CRDESC
FROM (
    SELECT
        r.MAINCODE,
        r.CRDESC,
        ROW_NUMBER() OVER (
            PARTITION BY r.MAINCODE
            ORDER BY r.INFOPUBLDATE DESC, r.CRDATE DESC, r.ID DESC
        ) AS rn
    FROM bond_bdcreditgrading r
    JOIN tmp_city_main cm
      ON cm.MAINCODE = r.MAINCODE
    WHERE r.INFOPUBLDATE < DATE_ADD(DATE('2025-04-30'), INTERVAL 1 DAY)
      AND (r.CRDATE IS NULL OR r.CRDATE < DATE('2025-05-01'))
      AND r.CRTYPECODE = 2
      AND r.CRSYSTEM = 1
      AND r.CRSTATUS = 1
      AND (
          r.RATINGEFFECTIVEENDDATE IS NULL
          OR r.RATINGEFFECTIVEENDDATE >= DATE('2025-04-30')
      )
) ranked
WHERE rn = 1
  AND CRDESC IN ('AAA', 'AA+');

ALTER TABLE tmp_rating ADD PRIMARY KEY (MAINCODE);

DROP TEMPORARY TABLE IF EXISTS tmp_city_rating;
CREATE TEMPORARY TABLE tmp_city_rating AS
SELECT
    cc.MAINCODE,
    cc.INNERCODE,
    cc.SECUMARKET,
    cc.SECUCODE,
    cc.SECUABBR,
    cc.ISSUER,
    cc.REGION,
    r.CRDESC
FROM tmp_city_code cc
JOIN tmp_rating r
  ON r.MAINCODE = cc.MAINCODE;

ALTER TABLE tmp_city_rating ADD INDEX idx_cr_main (MAINCODE), ADD INDEX idx_cr_inner (INNERCODE);

DROP TEMPORARY TABLE IF EXISTS tmp_current_raw;
CREATE TEMPORARY TABLE tmp_current_raw AS
SELECT
    cr.MAINCODE,
    cr.INNERCODE,
    cr.SECUMARKET,
    cr.SECUCODE,
    cr.SECUABBR,
    cr.ISSUER,
    cr.REGION,
    cr.CRDESC,
    DATE(v.ENDDATE) AS valuation_date,
    v.VPYIELD,
    v.TRUEREMAINMATURITY,
    v.UPDATETIME,
    v.ID
FROM tmp_city_rating cr
JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
  ON v.INNERCODE = cr.INNERCODE
WHERE v.ENDDATE >= DATE('2025-04-30')
  AND v.ENDDATE < DATE_ADD(DATE('2025-04-30'), INTERVAL 1 DAY)
  AND v.CREDIBILITYCODE = 1
  AND v.INSERTTIME < DATE_ADD(DATE('2025-04-30'), INTERVAL 1 DAY)
;

ALTER TABLE tmp_current_raw ADD INDEX idx_cur_raw_main (MAINCODE);

DROP TEMPORARY TABLE IF EXISTS tmp_current;
CREATE TEMPORARY TABLE tmp_current AS
SELECT
    MAINCODE,
    INNERCODE,
    SECUMARKET,
    SECUCODE,
    SECUABBR,
    ISSUER,
    REGION,
    CRDESC,
    valuation_date,
    VPYIELD,
    TRUEREMAINMATURITY
FROM (
    SELECT
        cur.*,
        ROW_NUMBER() OVER (
            PARTITION BY cur.MAINCODE
            ORDER BY
                CASE
                    WHEN cur.SECUMARKET = 89 THEN 0
                    WHEN cur.SECUMARKET = 83 THEN 1
                    WHEN cur.SECUMARKET = 90 THEN 2
                    ELSE 3
                END,
                COALESCE(cur.SECUCODE, ''),
                cur.INNERCODE,
                cur.UPDATETIME DESC,
                cur.ID DESC
        ) AS rn
    FROM tmp_current_raw cur
) ranked
WHERE rn = 1 AND VPYIELD IS NOT NULL AND TRUEREMAINMATURITY BETWEEN 1 AND 5;

ALTER TABLE tmp_current ADD PRIMARY KEY (MAINCODE);

SELECT
    MAINCODE,
    INNERCODE,
    SECUMARKET,
    SECUCODE,
    SECUABBR,
    ISSUER,
    REGION,
    CRDESC,
    valuation_date,
    VPYIELD,
    TRUEREMAINMATURITY
FROM tmp_current c
ORDER BY SECUCODE ASC;
