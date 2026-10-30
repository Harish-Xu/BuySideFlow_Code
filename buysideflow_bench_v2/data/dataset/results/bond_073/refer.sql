WITH params AS (
    SELECT
        DATE('2025-08-15') AS as_of_date,
        DATE('2025-06-30') AS report_date
),
credit_bond_ranked AS (
    SELECT
        bc.MAINCODE,
        bc.INNERCODE,
        bc.SECUCODE,
        bc.SECUABBR,
        bc.ISSUER,
        ROW_NUMBER() OVER (
            PARTITION BY bc.MAINCODE
            ORDER BY
                CASE WHEN bc.INNERCODE = bc.MAINCODE THEN 0 ELSE 1 END,
                CASE WHEN bc.SECUCODE REGEXP '^[0-9]{6}$' THEN 0 ELSE 1 END,
                bc.SECUCODE ASC,
                bc.INNERCODE ASC
        ) AS rn
    FROM bond_code bc
    JOIN params p
      ON 1 = 1
    WHERE (
            bc.BONDTYPELEVEL1 IN (1600,1700,1800,2500,2600,2800,2900,3100,3200,3300,3700)
            OR bc.BONDTYPELEVEL2 IN (1901,1903)
          )
      AND (bc.LISTEDDATE IS NULL OR DATE(bc.LISTEDDATE) <= p.as_of_date)
      AND (bc.INTERESTENDDATE IS NULL OR DATE(bc.INTERESTENDDATE) >= p.as_of_date)
      AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) >= p.as_of_date)
),
credit_bonds AS (
    SELECT
        MAINCODE,
        SECUCODE,
        SECUABBR,
        ISSUER
    FROM credit_bond_ranked
    WHERE rn = 1
),
fund_bond_ranked AS (
    SELECT
        f.INNERCODE AS fund_innercode,
        CAST(f.BONDCODE AS UNSIGNED) AS maincode,
        f.MARKETVALUE AS market_value_yuan,
        ROW_NUMBER() OVER (
            PARTITION BY f.INNERCODE, f.BONDCODE
            ORDER BY
                f.INFOPUBLDATE DESC,
                f.INSERTTIME DESC,
                f.ID DESC
        ) AS rn
    FROM mf_bondportifoliodetail f
    JOIN params p
      ON 1 = 1
    WHERE DATE(f.REPORTDATE) = p.report_date
      AND f.INFOPUBLDATE IS NOT NULL
      AND DATE(f.INFOPUBLDATE) <= p.as_of_date
      AND (f.INSERTTIME IS NULL OR DATE(f.INSERTTIME) <= p.as_of_date)
      AND f.MARKETVALUE IS NOT NULL
      AND f.MARKETVALUE > 0
      AND f.BONDCODE IS NOT NULL
),
fund_bond_holdings AS (
    SELECT
        fund_innercode,
        maincode,
        market_value_yuan
    FROM fund_bond_ranked
    WHERE rn = 1
),
holding_agg AS (
    SELECT
        h.maincode,
        COUNT(DISTINCT h.fund_innercode) AS fund_count,
        SUM(h.market_value_yuan) AS holding_market_value_yuan
    FROM fund_bond_holdings h
    JOIN credit_bonds cb
      ON h.maincode = cb.MAINCODE
    GROUP BY h.maincode
    HAVING COUNT(DISTINCT h.fund_innercode) >= 3
),
latest_size_ranked AS (
    SELECT
        s.MAINCODE,
        s.TOTALSIZE AS latest_size_mn,
        s.ENDDATE,
        s.INFOPUBLDATE,
        ROW_NUMBER() OVER (
            PARTITION BY s.MAINCODE
            ORDER BY
                DATE(s.ENDDATE) DESC,
                s.INFOPUBLDATE DESC,
                s.ID DESC
        ) AS rn
    FROM bond_size s
    JOIN params p
      ON 1 = 1
    JOIN holding_agg h
      ON s.MAINCODE = h.maincode
    WHERE DATE(s.ENDDATE) <= p.as_of_date
      AND s.INFOPUBLDATE IS NOT NULL
      AND DATE(s.INFOPUBLDATE) <= p.as_of_date
      AND s.TOTALSIZE IS NOT NULL
      AND s.TOTALSIZE > 0
),
result_rows AS (
    SELECT
        cb.SECUCODE AS bond_code,
        cb.SECUABBR AS bond_abbr,
        cb.ISSUER AS issuer,
        h.fund_count,
        h.holding_market_value_yuan,
        ls.latest_size_mn,
        h.holding_market_value_yuan / (ls.latest_size_mn * 1000000.0) AS concentration
    FROM holding_agg h
    JOIN credit_bonds cb
      ON h.maincode = cb.MAINCODE
    JOIN latest_size_ranked ls
      ON h.maincode = ls.MAINCODE
     AND ls.rn = 1
)
SELECT
    bond_code AS 债券代码,
    bond_abbr AS 债券简称,
    issuer AS 发行人,
    fund_count AS 重仓基金只数,
    ROUND(holding_market_value_yuan / 100000000.0, 2) AS 合计持仓市值,
    ROUND(latest_size_mn / 100.0, 2) AS 存量规模,
    ROUND(concentration * 100.0, 2) AS 重仓集中度
FROM result_rows
ORDER BY concentration DESC, bond_code ASC
LIMIT 20;
