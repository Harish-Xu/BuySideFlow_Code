WITH params AS (
    SELECT
        DATE('2025-07-31') AS as_of_date,
        DATE('2025-08-01') AS pressure_start_date,
        DATE('2025-12-31') AS pressure_end_date
),
candidate_bond_rows AS (
    SELECT DISTINCT
        bc.MAINCODE,
        bc.INNERCODE,
        bc.COMPANYCODE,
        bc.SECUCODE,
        COALESCE(bc.SECUABBR, bc.CHINAMEABBR, bc.CHINAME) AS bond_name,
        bc.ISSUER,
        DATE(bc.INTERESTENDDATE) AS maturity_date
    FROM bond_code bc
    CROSS JOIN params p
    JOIN bond_conceptnature cn
      ON cn.INNERCODE = bc.INNERCODE
     AND cn.CONCEPTTYPE = 40
     AND cn.CONNATURE = 4041
     AND (cn.BEGINDATE IS NULL OR DATE(cn.BEGINDATE) <= p.as_of_date)
     AND (cn.ENDDATE IS NULL OR DATE(cn.ENDDATE) > p.as_of_date)
    WHERE bc.MAINCODE IS NOT NULL
      AND bc.COMPANYCODE IS NOT NULL
      AND (bc.INTERESTENDDATE IS NULL OR DATE(bc.INTERESTENDDATE) > p.as_of_date)
      AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) > p.as_of_date)
),
candidate_companies AS (
    SELECT DISTINCT COMPANYCODE
    FROM candidate_bond_rows
),
direct_liuzhou_companies AS (
    SELECT DISTINCT cc.COMPANYCODE
    FROM candidate_companies cc
    CROSS JOIN params p
    JOIN lc_instiarchive ia
      ON ia.COMPANYCODE = cc.COMPANYCODE
     AND (ia.STARTDATE IS NULL OR DATE(ia.STARTDATE) <= p.as_of_date)
     AND (ia.CLOSEDATE IS NULL OR DATE(ia.CLOSEDATE) > p.as_of_date)
    WHERE ia.REGCITY = 450200
       OR ia.CONTACTCITY = 450200
       OR ia.CHINAME LIKE '柳州%'
       OR ia.CHINAME LIKE '广西柳州%'
       OR ia.CHINAME LIKE '广西柳州市%'
       OR ia.REGADDR LIKE '%柳州市%'
       OR ia.OFFICEADDR LIKE '%柳州市%'
       OR ia.CONTACTADDR LIKE '%柳州市%'
),
actual_controller_ranked AS (
    SELECT
        ep.COMPANYCODE,
        ep.RELATEDPARTYNAME,
        ep.RELATEDCODE,
        ROW_NUMBER() OVER (
            PARTITION BY ep.COMPANYCODE
            ORDER BY ep.INFOPUBLDATE DESC, ep.ENDDATE DESC, ep.ID DESC
        ) AS rn
    FROM candidate_companies cc
    JOIN lc_equitypenetrate ep
      ON ep.COMPANYCODE = cc.COMPANYCODE
    CROSS JOIN params p
    WHERE ep.RELATIONSHIP = 2
      AND ep.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND DATE(ep.ENDDATE) <= p.as_of_date
),
actual_liuzhou_companies AS (
    SELECT DISTINCT ac.COMPANYCODE
    FROM actual_controller_ranked ac
    CROSS JOIN params p
    LEFT JOIN lc_instiarchive ri
      ON ri.COMPANYCODE = ac.RELATEDCODE
     AND (ri.STARTDATE IS NULL OR DATE(ri.STARTDATE) <= p.as_of_date)
     AND (ri.CLOSEDATE IS NULL OR DATE(ri.CLOSEDATE) > p.as_of_date)
    WHERE ac.rn = 1
      AND (
          ac.RELATEDPARTYNAME LIKE '柳州%'
       OR ac.RELATEDPARTYNAME LIKE '%柳州市%'
       OR ri.REGCITY = 450200
       OR ri.CONTACTCITY = 450200
       OR ri.REGADDR LIKE '%柳州市%'
       OR ri.OFFICEADDR LIKE '%柳州市%'
       OR ri.CONTACTADDR LIKE '%柳州市%'
      )
),
liuzhou_companies AS (
    SELECT COMPANYCODE FROM direct_liuzhou_companies
    UNION
    SELECT COMPANYCODE FROM actual_liuzhou_companies
),
live_city_bonds AS (
    SELECT
        br.MAINCODE,
        br.COMPANYCODE,
        MIN(br.SECUCODE) AS bond_code,
        MIN(br.bond_name) AS bond_name,
        MIN(br.ISSUER) AS issuer,
        MIN(br.maturity_date) AS maturity_date
    FROM candidate_bond_rows br
    JOIN liuzhou_companies lc
      ON lc.COMPANYCODE = br.COMPANYCODE
    GROUP BY br.MAINCODE, br.COMPANYCODE
),
size_dates AS (
    SELECT
        s.MAINCODE,
        MAX(DATE(s.ENDDATE)) AS size_date
    FROM bond_size s
    JOIN live_city_bonds lb
      ON lb.MAINCODE = s.MAINCODE
    CROSS JOIN params p
    WHERE DATE(s.ENDDATE) <= p.as_of_date
      AND (s.INFOPUBLDATE IS NULL OR s.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
      AND s.TOTALSIZE IS NOT NULL
    GROUP BY s.MAINCODE
),
latest_size AS (
    SELECT
        MAINCODE,
        TOTALSIZE,
        ENDDATE
    FROM (
        SELECT
            s.MAINCODE,
            s.TOTALSIZE,
            s.ENDDATE,
            ROW_NUMBER() OVER (
                PARTITION BY s.MAINCODE
                ORDER BY s.UPDATETIME DESC, s.ID DESC
            ) AS rn
        FROM bond_size s
        JOIN size_dates sd
          ON sd.MAINCODE = s.MAINCODE
         AND sd.size_date = DATE(s.ENDDATE)
        CROSS JOIN params p
        WHERE s.TOTALSIZE IS NOT NULL
          AND (s.INFOPUBLDATE IS NULL OR s.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
    ) ranked
    WHERE rn = 1
      AND TOTALSIZE > 0
),
put_events AS (
    SELECT
        s.MAINCODE,
        MIN(DATE(s.ENDDATE)) AS put_date
    FROM bond_size s
    JOIN live_city_bonds lb
      ON lb.MAINCODE = s.MAINCODE
    CROSS JOIN params p
    WHERE s.ALTERATIONTYPE = 23
      AND DATE(s.ENDDATE) > p.as_of_date
      AND DATE(s.ENDDATE) <= p.pressure_end_date
      AND (s.INFOPUBLDATE IS NULL OR s.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
    GROUP BY s.MAINCODE
),
pressure_events AS (
    SELECT
        lb.MAINCODE AS maincode,
        lb.bond_code,
        lb.bond_name,
        lb.issuer,
        lb.maturity_date,
        pe.put_date,
        ls.TOTALSIZE AS size_million,
        CASE
            WHEN pe.put_date IS NOT NULL
             AND (lb.maturity_date IS NULL OR pe.put_date < lb.maturity_date)
            THEN pe.put_date
            ELSE lb.maturity_date
        END AS pressure_date
    FROM live_city_bonds lb
    JOIN latest_size ls
      ON ls.MAINCODE = lb.MAINCODE
    LEFT JOIN put_events pe
      ON pe.MAINCODE = lb.MAINCODE
)
SELECT
    maincode,
    bond_code,
    bond_name,
    issuer,
    maturity_date,
    put_date,
    size_million,
    pressure_date
FROM pressure_events
CROSS JOIN params p
WHERE pressure_date >= p.pressure_start_date
  AND pressure_date <= p.pressure_end_date
ORDER BY pressure_date, maincode;
