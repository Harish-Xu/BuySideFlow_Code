WITH params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
target_index AS (
    SELECT INNERCODE AS index_innercode
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '000300'
    LIMIT 1
),
hs300 AS (
    SELECT DISTINCT
        sm.INNERCODE AS secuinnercode,
        sm.COMPANYCODE AS companycode,
        sm.SECUCODE AS secucode,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, sm.SECUCODE) AS secuabbr,
        sm.LISTEDSECTOR
    FROM lc_indexcomponent ic
    JOIN target_index ti
      ON ti.index_innercode = ic.INDEXINNERCODE
    JOIN secumain sm
      ON sm.INNERCODE = ic.SECUINNERCODE
    JOIN params p
      ON ic.INDATE <= p.as_of_date
    WHERE (ic.OUTDATE IS NULL OR ic.OUTDATE > p.as_of_date)
      AND sm.SECUCATEGORY = 1
),
visible_eps_nonstib AS (
    SELECT
        m.COMPANYCODE,
        m.EPSTTM,
        m.ENDDATE,
        m.INFOPUBLDATE,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY m.INFOPUBLDATE DESC, m.INSERTTIME DESC, m.JSID DESC
        ) AS rn
    FROM lc_mainindexnew m
    JOIN hs300 h
      ON h.companycode = m.COMPANYCODE
     AND h.LISTEDSECTOR <> 7
    JOIN params p
      ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE m.ENDDATE <= p.as_of_date
      AND m.ENDDATE >= DATE_SUB(p.as_of_date, INTERVAL 3 YEAR)
),
financial_versions AS (
    SELECT
        d.COMPANYCODE, d.ENDDATE, d.INFOPUBLDATE,
        d.NPFROMPARENTCOMPANYOWNERS, d.TOTALSHARES,
        ROW_NUMBER() OVER (
            PARTITION BY d.COMPANYCODE, d.ENDDATE
            ORDER BY FIELD(d.MARK, 1, 8, 7, 6, 2),
                     d.INFOPUBLDATE DESC, d.INSERTTIME DESC, d.JSID DESC
        ) AS rn
    FROM lc_maindatanew d
    JOIN hs300 h ON h.companycode = d.COMPANYCODE AND h.LISTEDSECTOR <> 7
    CROSS JOIN params p
    WHERE d.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND d.ENDDATE BETWEEN DATE_SUB(p.as_of_date, INTERVAL 3 YEAR) AND p.as_of_date
      AND d.MARK IN (1, 8, 7, 6, 2)
      AND COALESCE(d.ACCOUNTINGSTANDARDS, 1) = 1
),
financials AS (
    SELECT * FROM financial_versions WHERE rn = 1
),
latest_nonstib_period AS (
    SELECT COMPANYCODE, MAX(ENDDATE) AS ENDDATE
    FROM (
        SELECT COMPANYCODE, ENDDATE FROM financials
        UNION ALL
        SELECT COMPANYCODE, ENDDATE FROM visible_eps_nonstib WHERE rn = 1
    ) periods
    GROUP BY COMPANYCODE
),
latest_eps_nonstib AS (
    -- Rebuild a missing latest-period EPS; never substitute an older report.
    SELECT
        p.COMPANYCODE,
        COALESCE(m.EPSTTM,
            CASE WHEN c.TOTALSHARES > 0 THEN
                CASE WHEN MONTH(p.ENDDATE) = 12 THEN c.NPFROMPARENTCOMPANYOWNERS
                     ELSE c.NPFROMPARENTCOMPANYOWNERS + a.NPFROMPARENTCOMPANYOWNERS
                          - b.NPFROMPARENTCOMPANYOWNERS
                END / c.TOTALSHARES
            END
        ) AS EPSTTM,
        p.ENDDATE,
        CASE WHEN m.EPSTTM IS NOT NULL THEN m.INFOPUBLDATE
             ELSE c.INFOPUBLDATE END AS INFOPUBLDATE
    FROM latest_nonstib_period p
    LEFT JOIN visible_eps_nonstib m
      ON m.COMPANYCODE = p.COMPANYCODE AND m.ENDDATE = p.ENDDATE AND m.rn = 1
    LEFT JOIN financials c
      ON c.COMPANYCODE = p.COMPANYCODE AND c.ENDDATE = p.ENDDATE
    LEFT JOIN financials a
      ON a.COMPANYCODE = p.COMPANYCODE
     AND YEAR(a.ENDDATE) = YEAR(p.ENDDATE) - 1 AND MONTH(a.ENDDATE) = 12
    LEFT JOIN financials b
      ON b.COMPANYCODE = p.COMPANYCODE
     AND b.ENDDATE = DATE_SUB(p.ENDDATE, INTERVAL 1 YEAR)
),
latest_eps_stib AS (
    SELECT
        m.COMPANYCODE,
        m.EPSTTM,
        m.ENDDATE,
        m.INFOPUBLDATE,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE
            ORDER BY m.ENDDATE DESC,
                     CASE WHEN m.IFADJUSTED = 1 THEN 0 ELSE 1 END,
                     m.INFOPUBLDATE DESC,
                     m.INSERTTIME DESC,
                     m.JSID DESC
        ) AS rn
    FROM lc_stibmainindex m
    JOIN hs300 h
      ON h.companycode = m.COMPANYCODE
     AND h.LISTEDSECTOR = 7
    JOIN params p
      ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE m.IFMERGED = 1 AND m.ENDDATE <= p.as_of_date
),
latest_eps AS (
    SELECT COMPANYCODE, EPSTTM, ENDDATE, INFOPUBLDATE
    FROM latest_eps_nonstib WHERE EPSTTM IS NOT NULL
    UNION ALL
    SELECT COMPANYCODE, EPSTTM, ENDDATE, INFOPUBLDATE
    FROM latest_eps_stib WHERE rn = 1 AND EPSTTM IS NOT NULL
),
eps_rank AS (
    SELECT
        h.secucode,
        h.secuabbr,
        h.companycode,
        h.LISTEDSECTOR,
        e.EPSTTM,
        e.ENDDATE AS latest_enddate,
        e.INFOPUBLDATE AS latest_infopubldate,
        PERCENT_RANK() OVER (ORDER BY e.EPSTTM DESC) AS eps_percent_rank
    FROM hs300 h
    JOIN latest_eps e
      ON e.COMPANYCODE = h.companycode
),
quarterly_gm_ranked AS (
    SELECT
        q.COMPANYCODE,
        q.ENDDATE,
        q.GROSSINCOMERATIO,
        q.INFOPUBLDATE,
        ROW_NUMBER() OVER (
            PARTITION BY q.COMPANYCODE, q.ENDDATE
            ORDER BY q.INFOPUBLDATE DESC,
                     q.INSERTTIME DESC,
                     q.JSID DESC
        ) AS rn
    FROM lc_qfinancialindexnew q
    JOIN eps_rank r
     ON r.companycode = q.COMPANYCODE
     AND r.eps_percent_rank <= 0.25
     AND q.ENDDATE <= r.latest_enddate
     AND q.ENDDATE > DATE_SUB(r.latest_enddate, INTERVAL 2 YEAR)
    JOIN params p
      ON q.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE q.ACCOUNTINGSTANDARDS = 1
      AND MONTH(q.ENDDATE) IN (3, 6, 9, 12)
),
quarterly_gm AS (
    SELECT * FROM quarterly_gm_ranked WHERE rn = 1
),
last8_quarters AS (
    SELECT
        r.secucode,
        r.secuabbr,
        r.companycode,
        r.EPSTTM,
        r.latest_enddate,
        r.latest_infopubldate,
        q.ENDDATE,
        q.GROSSINCOMERATIO,
        ROW_NUMBER() OVER (
            PARTITION BY r.companycode
            ORDER BY q.ENDDATE DESC, q.INFOPUBLDATE DESC
        ) AS rn
    FROM eps_rank r
    JOIN quarterly_gm q
      ON q.COMPANYCODE = r.companycode
    WHERE r.eps_percent_rank <= 0.25
),
gm_agg AS (
    SELECT
        companycode,
        MAX(secucode) AS secucode,
        MAX(secuabbr) AS secuabbr,
        MAX(EPSTTM) AS EPSTTM,
        MAX(latest_enddate) AS latest_enddate,
        MAX(latest_infopubldate) AS latest_infopubldate,
        STDDEV_POP(GROSSINCOMERATIO) AS gross_margin_stability,
        COUNT(*) AS n_obs
    FROM last8_quarters
    WHERE rn <= 8
    GROUP BY companycode
    HAVING COUNT(*) = 8
       AND COUNT(GROSSINCOMERATIO) = 8
       AND MAX(YEAR(ENDDATE) * 4 + QUARTER(ENDDATE))
           - MIN(YEAR(ENDDATE) * 4 + QUARTER(ENDDATE)) = 7
       AND MAX(ENDDATE) = MAX(latest_enddate)
),
gm_rank AS (
    SELECT
        *,
        PERCENT_RANK() OVER (ORDER BY gross_margin_stability ASC) AS stability_percent_rank
    FROM gm_agg
)
SELECT
    DATE_FORMAT(latest_infopubldate,'%Y-%m-%d') AS `最新报告期发布日`,
    secucode AS `股票代码`,
    secuabbr AS `股票简称`,
    ROUND(EPSTTM, 4) AS `EPS_TTM`,
    ROUND(gross_margin_stability, 4) AS `单季毛利率稳定性`
FROM gm_rank
WHERE stability_percent_rank <= 0.25
ORDER BY EPSTTM DESC, gross_margin_stability ASC, secucode ASC;
