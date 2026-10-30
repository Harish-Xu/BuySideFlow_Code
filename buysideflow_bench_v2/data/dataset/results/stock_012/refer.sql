WITH params AS (
    SELECT DATE('2025-11-12') AS as_of_date
),
latest_td AS (
    SELECT MAX(v.TRADINGDAY) AS td
    FROM lc_dindicesforvaluation v
    JOIN params p
      ON v.TRADINGDAY <= p.as_of_date
),
industry_asof AS (
    SELECT COMPANYCODE, FIRSTINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY
                    e.INFOPUBLDATE DESC,
                    e.INSERTTIME DESC,
                    e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    ) t
    WHERE rn = 1
),
stock_pool AS (
    SELECT COMPANYCODE, INNERCODE, stock_code, stock_name
    FROM (
        SELECT
            s.COMPANYCODE,
            s.INNERCODE,
            s.SECUCODE AS stock_code,
            COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS stock_name,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY CASE WHEN s.LISTEDSECTOR = 1 THEN 0 ELSE 1 END, s.SECUCODE
            ) AS rn
        FROM secumain s
        JOIN industry_asof i
          ON i.COMPANYCODE = s.COMPANYCODE
        JOIN params p
          ON 1 = 1
        WHERE s.SECUCATEGORY = 1
          AND s.SECUMARKET IN (83, 90)
          AND s.LISTEDSECTOR IN (1, 2, 6)
          AND NOT EXISTS (
              SELECT 1
              FROM cs_riskalert ra
              WHERE ra.INNERCODE = s.INNERCODE
                AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
                AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
                AND DATE(ra.IMPLEMENTDATE) <= p.as_of_date
                AND (
                    ra.REMOVEDATE IS NULL
                    OR DATE(ra.REMOVEDATE) > p.as_of_date
                    OR DATE(COALESCE(ra.REMOVEINFOPUBLDATE, ra.REMOVEDATE)) > p.as_of_date
                )
          )
          AND i.FIRSTINDUSTRYNAME NOT IN ('银行', '非银金融', '房地产')
    ) x
    WHERE rn = 1
),
latest_mv AS (
    SELECT v.INNERCODE, v.TOTALMV
    FROM lc_dindicesforvaluation v
    JOIN latest_td l
      ON l.td = v.TRADINGDAY
    WHERE v.TOTALMV > 0
),
bs_raw AS (
    SELECT
        b.COMPANYCODE,
        DATE(b.ENDDATE) AS end_date,
        b.GOODWILL,
        b.SEWITHOUTMI,
        ROW_NUMBER() OVER (
            PARTITION BY b.COMPANYCODE, DATE(b.ENDDATE)
            ORDER BY b.INFOPUBLDATE DESC, b.ID DESC
        ) AS rn
    FROM lc_balancesheetall b
    JOIN stock_pool sp
      ON sp.COMPANYCODE = b.COMPANYCODE
    JOIN params p
      ON b.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE b.IFMERGED = 1
      AND b.IFCOMPLETE = 1
),
bs_q AS (
    SELECT COMPANYCODE, end_date, GOODWILL, SEWITHOUTMI
    FROM bs_raw
    WHERE rn = 1
),
cash_raw AS (
    SELECT
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS end_date,
        m.ENDPERIODCASHEQUIVALENT,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
            ORDER BY
                CASE m.MARK
                    WHEN 1 THEN 0
                    WHEN 8 THEN 1
                    WHEN 7 THEN 2
                    WHEN 6 THEN 3
                    WHEN 2 THEN 4
                    ELSE 9
                END,
                m.INFOPUBLDATE DESC,
                m.INSERTTIME DESC,
                m.JSID DESC,
                m.ID DESC
        ) AS rn
    FROM lc_maindatanew m
    JOIN stock_pool sp
      ON sp.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE m.ENDDATE <= p.as_of_date
      AND MONTH(m.ENDDATE) IN (3, 6, 9, 12)
      AND m.MARK IN (1, 8, 7, 6, 2)
),
cash_q AS (
    SELECT COMPANYCODE, end_date, ENDPERIODCASHEQUIVALENT
    FROM cash_raw
    WHERE rn = 1
),
debt_raw AS (
    SELECT
        d.COMPANYCODE,
        DATE(d.ENDDATE) AS end_date,
        d.INTERESTBEARDEBT,
        ROW_NUMBER() OVER (
            PARTITION BY d.COMPANYCODE, DATE(d.ENDDATE)
            ORDER BY d.INFOPUBLDATE DESC, d.ID DESC
        ) AS rn
    FROM lc_derivativedata d
    JOIN stock_pool sp
      ON sp.COMPANYCODE = d.COMPANYCODE
    JOIN params p
      ON d.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
),
debt_q AS (
    SELECT COMPANYCODE, end_date, INTERESTBEARDEBT
    FROM debt_raw
    WHERE rn = 1
),
latest_fin AS (
    SELECT
        COMPANYCODE,
        end_date,
        ENDPERIODCASHEQUIVALENT,
        GOODWILL,
        SEWITHOUTMI,
        INTERESTBEARDEBT
    FROM (
        SELECT
            b.COMPANYCODE,
            b.end_date,
            c.ENDPERIODCASHEQUIVALENT,
            b.GOODWILL,
            b.SEWITHOUTMI,
            d.INTERESTBEARDEBT,
            ROW_NUMBER() OVER (
                PARTITION BY b.COMPANYCODE
                ORDER BY b.end_date DESC
            ) AS rn
        FROM (
            SELECT
                bs.*,
                ROW_NUMBER() OVER (
                    PARTITION BY bs.COMPANYCODE
                    ORDER BY bs.end_date DESC
                ) AS period_rn
            FROM bs_q bs
        ) b
        LEFT JOIN cash_q c
          ON c.COMPANYCODE = b.COMPANYCODE
         AND c.end_date = b.end_date
        LEFT JOIN debt_q d
          ON d.COMPANYCODE = b.COMPANYCODE
         AND d.end_date = b.end_date
        WHERE b.period_rn = 1
    ) t
    WHERE rn = 1
),
ocf_raw AS (
    SELECT
        m.COMPANYCODE,
        DATE(m.ENDDATE) AS end_date,
        m.NETOPERATECASHFLOW,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, DATE(m.ENDDATE)
            ORDER BY
                CASE m.MARK
                    WHEN 1 THEN 0
                    WHEN 8 THEN 1
                    WHEN 7 THEN 2
                    WHEN 6 THEN 3
                    WHEN 2 THEN 4
                    ELSE 9
                END,
                m.INFOPUBLDATE DESC,
                m.ID DESC
        ) AS rn
    FROM lc_maindatanew m
    JOIN stock_pool sp
      ON sp.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE MONTH(m.ENDDATE) IN (3, 6, 9, 12)
      AND m.ENDDATE >= DATE_SUB(p.as_of_date, INTERVAL 2 YEAR)
      AND m.MARK IN (1, 8, 7, 6, 2)
      AND m.NETOPERATECASHFLOW IS NOT NULL
),
ocf_q AS (
    SELECT
        COMPANYCODE,
        end_date,
        CASE
            WHEN MONTH(end_date) = 3 THEN NETOPERATECASHFLOW
            ELSE NETOPERATECASHFLOW
                 - LAG(NETOPERATECASHFLOW) OVER (
                     PARTITION BY COMPANYCODE, YEAR(end_date)
                     ORDER BY end_date
                   )
        END AS ocf_single_q,
        ROW_NUMBER() OVER (PARTITION BY COMPANYCODE ORDER BY end_date DESC) AS rn_desc
    FROM ocf_raw
    WHERE rn = 1
),
ocf_4q AS (
    SELECT
        COMPANYCODE,
        SUM(ocf_single_q) AS ocf_sum_4q,
        COUNT(ocf_single_q) AS q_cnt,
        MAX(end_date) AS latest_ocf_end_date
    FROM ocf_q
    WHERE rn_desc <= 4
    GROUP BY COMPANYCODE
    HAVING COUNT(ocf_single_q) = 4
       AND PERIOD_DIFF(
               EXTRACT(YEAR_MONTH FROM MAX(end_date)),
               EXTRACT(YEAR_MONTH FROM MIN(end_date))
           ) = 9
)
SELECT
    sp.stock_code AS 代码,
    sp.stock_name AS 名称,
    ROUND(mv.TOTALMV, 2) AS 总市值,
    ROUND(lf.ENDPERIODCASHEQUIVALENT - lf.INTERESTBEARDEBT, 2) AS 净现金,
    ROUND(mv.TOTALMV / NULLIF(lf.ENDPERIODCASHEQUIVALENT - lf.INTERESTBEARDEBT, 0), 4) AS 市值净现金比,
    ROUND(o.ocf_sum_4q, 2) AS 最近4个单季度经营现金流合计,
    ROUND(lf.GOODWILL, 2) AS 商誉金额
FROM stock_pool sp
JOIN latest_mv mv
  ON mv.INNERCODE = sp.INNERCODE
JOIN latest_fin lf
  ON lf.COMPANYCODE = sp.COMPANYCODE
JOIN ocf_4q o
  ON o.COMPANYCODE = sp.COMPANYCODE
 AND o.latest_ocf_end_date = lf.end_date
WHERE (lf.ENDPERIODCASHEQUIVALENT - lf.INTERESTBEARDEBT) > 0
  AND mv.TOTALMV < 2.0 * (lf.ENDPERIODCASHEQUIVALENT - lf.INTERESTBEARDEBT)
  AND o.ocf_sum_4q > 0
  AND (
      (lf.SEWITHOUTMI > 0
       AND lf.GOODWILL IS NOT NULL
       AND lf.GOODWILL / lf.SEWITHOUTMI < 0.05)
      OR (lf.GOODWILL IS NOT NULL AND lf.GOODWILL < 100000000)
  )
ORDER BY 市值净现金比 ASC, 最近4个单季度经营现金流合计 DESC, 代码 ASC;
