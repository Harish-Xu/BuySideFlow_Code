WITH params AS (
    SELECT
        DATE('2025-08-29') AS as_of_date,
        TIMESTAMP('2025-08-30 00:00:00') AS next_day_ts
),
active_codes AS (
    SELECT
        bc.MAINCODE,
        bc.INNERCODE,
        bc.COMPANYCODE,
        bc.SECUCODE AS bond_code,
        bc.SECUABBR AS bond_name,
        bc.CHINAME AS bond_full_name,
        bc.ISSUER AS issuer,
        bc.SECUMARKET
    FROM bond_code bc
    CROSS JOIN params p
    WHERE bc.MAINCODE IS NOT NULL
      AND bc.INNERCODE IS NOT NULL
      AND bc.COMPANYCODE IS NOT NULL
      AND bc.ISSUER IS NOT NULL
      AND (
            bc.BONDTYPELEVEL1 IN (1600, 1700, 1800, 2500, 2600, 2800, 2900, 3100, 3200, 3300, 3700)
         OR bc.BONDTYPELEVEL2 IN (1901, 1903)
      )
      AND COALESCE(bc.BONDTYPELEVEL1DESC, '') NOT IN (
          '国债', '地方政府债券', '政策性银行债', '央行票据',
          '资产支持证券', '可转债', '可交换公司债券',
          '分离交易可转债', '信用风险缓释凭证', '信用联结票据'
      )
      AND COALESCE(bc.BONDTYPELEVEL2DESC, '') NOT IN (
          '同业存单', '可转债', '可交换公司债券',
          '分离交易可转债', '信用风险缓释凭证', '信用联结票据'
      )
      AND COALESCE(bc.BONDTYPELEVEL1DESC, '') NOT LIKE '%资产支持%'
      AND COALESCE(bc.BONDTYPELEVEL2DESC, '') NOT LIKE '%资产支持%'
      AND COALESCE(bc.BONDTYPELEVEL1DESC, '') NOT LIKE '%次级%'
      AND COALESCE(bc.BONDTYPELEVEL2DESC, '') NOT LIKE '%次级%'
      AND COALESCE(bc.SECUABBR, '') NOT LIKE '%次级%'
      AND COALESCE(bc.CHINAME, '') NOT LIKE '%次级%'
      AND COALESCE(bc.SECUABBR, '') NOT LIKE '%永续%'
      AND COALESCE(bc.CHINAME, '') NOT LIKE '%永续%'
      AND COALESCE(bc.SECUABBR, '') NOT LIKE '%可续%'
      AND COALESCE(bc.CHINAME, '') NOT LIKE '%可续%'
      AND (bc.LISTEDDATE IS NULL OR bc.LISTEDDATE <= p.as_of_date)
      AND (bc.INTERESTENDDATE IS NULL OR bc.INTERESTENDDATE >= p.as_of_date)
      AND (bc.DELISTDATE IS NULL OR bc.DELISTDATE >= p.as_of_date)
),
basic_ranked AS (
    SELECT
        bi.MAINCODE,
        bi.OPTIONTYPE,
        bi.RATEADJUSTMODE,
        ROW_NUMBER() OVER (
            PARTITION BY bi.MAINCODE
            ORDER BY bi.UPDATETIME DESC, bi.ID DESC
        ) AS rn
    FROM bond_basicinfo bi
    CROSS JOIN params p
    WHERE (bi.INITIALINFOPUBLDATE IS NULL OR bi.INITIALINFOPUBLDATE <= p.as_of_date)
),
clean_maincodes AS (
    SELECT DISTINCT
        ac.MAINCODE
    FROM active_codes ac
    LEFT JOIN basic_ranked bi
      ON bi.MAINCODE = ac.MAINCODE
     AND bi.rn = 1
    WHERE COALESCE(bi.OPTIONTYPE, '') NOT LIKE '%回售%'
      AND COALESCE(bi.OPTIONTYPE, '') NOT LIKE '%赎回%'
      AND COALESCE(bi.OPTIONTYPE, '') NOT LIKE '%调整票面利率%'
      AND COALESCE(bi.OPTIONTYPE, '') NOT LIKE '%调息%'
      AND COALESCE(bi.OPTIONTYPE, '') NOT LIKE '%续期%'
      AND COALESCE(bi.OPTIONTYPE, '') NOT LIKE '%利息递延%'
      AND bi.RATEADJUSTMODE IS NULL
),
latest_rating AS (
    SELECT
        maincode
    FROM (
        SELECT
            CAST(g.MAINCODE AS UNSIGNED) AS maincode,
            g.CRDESC AS rating,
            ROW_NUMBER() OVER (
                PARTITION BY g.MAINCODE
                ORDER BY g.INFOPUBLDATE DESC, g.CRDATE DESC, g.UPDATETIME DESC, g.ID DESC
            ) AS rn
        FROM bond_bdcreditgrading g
        CROSS JOIN params p
        WHERE g.INFOPUBLDATE <= p.as_of_date
          AND g.CRTYPECODE = 2
          AND g.CRSYSTEM = 1
          AND (g.RATINGEFFECTIVEENDDATE IS NULL OR g.RATINGEFFECTIVEENDDATE >= p.as_of_date)
    ) ranked
    WHERE rn = 1
      AND rating = 'AAA'
)
SELECT
    p.as_of_date AS valuation_date,
    CAST(ac.MAINCODE AS UNSIGNED) AS maincode,
    CAST(ac.INNERCODE AS UNSIGNED) AS innercode,
    CAST(ac.COMPANYCODE AS UNSIGNED) AS companycode,
    ac.bond_code,
    ac.bond_name,
    ac.issuer,
    ac.SECUMARKET AS secumarket
FROM params p
CROSS JOIN active_codes ac
JOIN clean_maincodes cm
  ON cm.MAINCODE = ac.MAINCODE
JOIN latest_rating r
  ON r.maincode = ac.MAINCODE
ORDER BY ac.MAINCODE, ac.INNERCODE;
