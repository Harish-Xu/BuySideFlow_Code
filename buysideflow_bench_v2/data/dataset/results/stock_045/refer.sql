WITH
params AS (
    SELECT
        DATE('2025-10-29') AS as_of_date,
        DATE('2025-09-30') AS latest_financial_end
),
target_index AS (
    SELECT s.INNERCODE
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE = '000300'
    ORDER BY s.INNERCODE
    LIMIT 1
),
index_constituents AS (
    SELECT DISTINCT
        ic.SECUINNERCODE AS innercode,
        sm.COMPANYCODE
    FROM lc_indexcomponent ic
    JOIN target_index ti
      ON ti.INNERCODE = ic.INDEXINNERCODE
    JOIN params p
      ON ic.INDATE <= p.as_of_date
    JOIN secumain sm
      ON sm.INNERCODE = ic.SECUINNERCODE
    WHERE (ic.OUTDATE IS NULL OR ic.OUTDATE > p.as_of_date)
      AND sm.SECUCATEGORY = 1
      AND sm.LISTEDSECTOR = 1
),
stock_info AS (
    SELECT
        c.innercode AS INNERCODE,
        c.COMPANYCODE,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS company
    FROM index_constituents c
    JOIN secumain s
      ON s.INNERCODE = c.innercode
),
latest_fin AS (
    SELECT
        COMPANYCODE,
        ROE,
        ROA,
        GROSSINCOMERATIO,
        OPERATINGREVENUEGROWRATE
    FROM (
        SELECT
            m.COMPANYCODE,
            m.ROE,
            m.ROA,
            m.GROSSINCOMERATIO,
            m.OPERATINGREVENUEGROWRATE,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE
                ORDER BY m.ENDDATE DESC,
                         m.INFOPUBLDATE DESC,
                         COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                         m.JSID DESC
            ) AS rn
        FROM lc_mainindexnew m
        JOIN stock_info si
          ON si.COMPANYCODE = m.COMPANYCODE
        JOIN params p
          ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE m.ENDDATE <= p.latest_financial_end
          AND m.ROE IS NOT NULL
          AND m.ROA IS NOT NULL
          AND m.GROSSINCOMERATIO IS NOT NULL
          AND m.OPERATINGREVENUEGROWRATE IS NOT NULL
    ) t
    WHERE rn = 1
),
trade_asof AS (
    SELECT MAX(v.TRADINGDAY) AS trade_date
    FROM lc_dindicesforvaluation v
    JOIN index_constituents c
      ON c.innercode = v.INNERCODE
    JOIN params p
      ON v.TRADINGDAY <= p.as_of_date
),
latest_val AS (
    SELECT
        c.COMPANYCODE,
        v.PE AS PE_TTM,
        v.PB
    FROM (
        SELECT
            v.INNERCODE,
            MAX(v.JSID) AS max_jsid
        FROM lc_dindicesforvaluation v
        JOIN trade_asof ta
          ON v.TRADINGDAY = ta.trade_date
        JOIN index_constituents c
          ON c.innercode = v.INNERCODE
        WHERE v.PE IS NOT NULL
          AND v.PB IS NOT NULL
          AND v.PE > 0
          AND v.PB > 0
        GROUP BY v.INNERCODE
    ) k
    JOIN lc_dindicesforvaluation v
      ON v.INNERCODE = k.INNERCODE
     AND v.JSID = k.max_jsid
    JOIN index_constituents c
      ON c.innercode = k.INNERCODE
),
base AS (
    SELECT
        si.SECUCODE,
        si.company,
        lf.ROE,
        lf.ROA,
        lf.GROSSINCOMERATIO,
        lv.PE_TTM,
        lv.PB,
        lf.OPERATINGREVENUEGROWRATE
    FROM stock_info si
    JOIN latest_fin lf
      ON lf.COMPANYCODE = si.COMPANYCODE
    JOIN latest_val lv
      ON lv.COMPANYCODE = si.COMPANYCODE
),
scored AS (
    SELECT
        b.*,
        COALESCE((b.ROE - AVG(b.ROE) OVER ()) / NULLIF(STDDEV_POP(b.ROE) OVER (), 0), 0) AS z_roe,
        COALESCE((b.ROA - AVG(b.ROA) OVER ()) / NULLIF(STDDEV_POP(b.ROA) OVER (), 0), 0) AS z_roa,
        COALESCE((b.GROSSINCOMERATIO - AVG(b.GROSSINCOMERATIO) OVER ()) / NULLIF(STDDEV_POP(b.GROSSINCOMERATIO) OVER (), 0), 0) AS z_gm,
        COALESCE((AVG(b.PE_TTM) OVER () - b.PE_TTM) / NULLIF(STDDEV_POP(b.PE_TTM) OVER (), 0), 0) AS z_pe,
        COALESCE((AVG(b.PB) OVER () - b.PB) / NULLIF(STDDEV_POP(b.PB) OVER (), 0), 0) AS z_pb,
        COALESCE((b.OPERATINGREVENUEGROWRATE - AVG(b.OPERATINGREVENUEGROWRATE) OVER ()) / NULLIF(STDDEV_POP(b.OPERATINGREVENUEGROWRATE) OVER (), 0), 0) AS z_growth
    FROM base b
)
SELECT
    SECUCODE AS `代码`,
    company AS `公司`,
    ROUND(
        0.4 * ((z_roe + z_roa + z_gm) / 3.0) +
        0.3 * ((z_pe + z_pb) / 2.0) +
        0.3 * z_growth,
        6
    ) AS `综合得分`,
    ROUND(ROE, 4) AS `ROE`,
    ROUND(ROA, 4) AS `ROA`,
    ROUND(GROSSINCOMERATIO, 4) AS `毛利率`,
    ROUND(PE_TTM, 4) AS `PE`,
    ROUND(PB, 4) AS `PB`,
    ROUND(OPERATINGREVENUEGROWRATE, 4) AS `营业收入同比增速`
FROM scored
ORDER BY `综合得分` DESC, `代码`
LIMIT 30;
