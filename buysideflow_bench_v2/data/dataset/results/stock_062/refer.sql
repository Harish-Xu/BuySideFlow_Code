WITH
params AS (
    SELECT
        DATE('2025-12-31') AS as_of_date,
        TIMESTAMP('2025-12-31 23:59:59') AS as_of_dt,
        DATE('2025-09-30') AS latest_end,
        DATE('2024-09-30') AS prev_end
),
listed_asof AS (
    SELECT INNERCODE, CHANGETYPE
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY ls.CHANGEDATE DESC,
                         COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
                         ls.JSID DESC,
                         ls.ID DESC
            ) AS rn
        FROM (
            SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID, SECUMARKET
            FROM lc_liststatus
            UNION ALL
            SELECT INNERCODE, CHANGEDATE, CHANGETYPE, INSERTTIME, JSID, ID, 83 AS SECUMARKET
            FROM lc_stibliststatus
        ) ls
        JOIN params p
          ON ls.CHANGEDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND (ls.INSERTTIME IS NULL OR ls.INSERTTIME < DATE_ADD(p.as_of_date, INTERVAL 1 DAY))
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
stock_base AS (
    SELECT DISTINCT
        s.COMPANYCODE,
        s.LISTEDSECTOR
    FROM secumain s
    JOIN params p
      ON s.LISTEDDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    LEFT JOIN listed_asof la
      ON la.INNERCODE = s.INNERCODE
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND la.CHANGETYPE IN (1, 3)
),
industry_asof AS (
    SELECT
        COMPANYCODE,
        FIRSTINDUSTRYNAME
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC,
                         e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR <> 7
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_date
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
          AND e.FIRSTINDUSTRYNAME IN (
               '有色金属', '基础化工', '建筑材料'
          )
        UNION ALL
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
            ) AS rn
        FROM lc_stibexgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
         AND sb.LISTEDSECTOR = 7
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_date
        WHERE e.STANDARD = 38
          AND e.EFFECTIVEDATE <= p.as_of_date
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
          AND e.FIRSTINDUSTRYNAME IN (
              '有色金属', '基础化工', '建筑材料'
          )
    ) t
    WHERE rn = 1
),
candidate AS (
    SELECT
        sb.COMPANYCODE,
        ia.FIRSTINDUSTRYNAME AS industry
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
),
bs_ranked AS (
    SELECT
        b.COMPANYCODE,
        b.ENDDATE,
        CASE
            WHEN b.CONTRACTLIABILITY IS NULL AND b.ADVANCERECEIPTS IS NULL THEN NULL
            ELSE COALESCE(b.CONTRACTLIABILITY, 0) + COALESCE(b.ADVANCERECEIPTS, 0)
        END AS presale_liability,
        ROW_NUMBER() OVER (
            PARTITION BY b.COMPANYCODE, b.ENDDATE
            ORDER BY b.INFOPUBLDATE DESC,
                     COALESCE(b.INSERTTIME, b.INFOPUBLDATE) DESC,
                     b.JSID DESC,
                     b.ID DESC
        ) AS rn
    FROM lc_balancesheetall b
    JOIN candidate c
      ON c.COMPANYCODE = b.COMPANYCODE
    JOIN params p
      ON b.INFOPUBLDATE <= p.as_of_dt
    WHERE b.ENDDATE IN (p.latest_end, p.prev_end)
      AND b.IFMERGED = 1
      AND b.IFADJUSTED = 2
      AND b.ACCOUNTINGSTANDARDS = 1
),
bs AS (
    SELECT
        COMPANYCODE,
        ENDDATE,
        presale_liability
    FROM bs_ranked
    WHERE rn = 1
),
presale_yoy AS (
    SELECT
        cur.COMPANYCODE,
        (cur.presale_liability / NULLIF(prev.presale_liability, 0) - 1) * 100 AS presale_yoy
    FROM bs cur
    JOIN bs prev
     ON prev.COMPANYCODE = cur.COMPANYCODE
    JOIN params p
      ON cur.ENDDATE = p.latest_end
     AND prev.ENDDATE = p.prev_end
    WHERE cur.presale_liability IS NOT NULL
      AND prev.presale_liability > 0
),
revenue_ranked AS (
    SELECT
        m.COMPANYCODE,
        m.ENDDATE,
        m.OPERATINGREENUE,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY CASE m.MARK
                         WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                         WHEN 6 THEN 4 WHEN 2 THEN 5 ELSE 9
                     END,
                     m.INFOPUBLDATE DESC,
                     COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                     m.JSID DESC
        ) AS rn
    FROM lc_maindatanew m
    JOIN candidate c
      ON c.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.INFOPUBLDATE <= p.as_of_dt
    WHERE m.MARK IN (1, 8, 7, 6, 2)
      AND m.ACCOUNTINGSTANDARDS = 1
      AND m.ENDDATE IN (p.latest_end, p.prev_end)
      AND m.OPERATINGREENUE IS NOT NULL
),
revenue_period AS (
    SELECT COMPANYCODE, ENDDATE, OPERATINGREENUE
    FROM revenue_ranked
    WHERE rn = 1
),
latest_rev AS (
    SELECT
        cur.COMPANYCODE,
        (cur.OPERATINGREENUE / NULLIF(prev.OPERATINGREENUE, 0) - 1) * 100 AS revenue_yoy
    FROM revenue_period cur
    JOIN revenue_period prev
      ON prev.COMPANYCODE = cur.COMPANYCODE
    JOIN params p
      ON cur.ENDDATE = p.latest_end
     AND prev.ENDDATE = p.prev_end
    WHERE cur.OPERATINGREENUE IS NOT NULL
      AND prev.OPERATINGREENUE > 0
),
scored AS (
    SELECT
        c.industry,
        py.presale_yoy,
        lr.revenue_yoy
    FROM candidate c
    JOIN presale_yoy py
      ON py.COMPANYCODE = c.COMPANYCODE
    JOIN latest_rev lr
      ON lr.COMPANYCODE = c.COMPANYCODE
),
industry_stats AS (
    SELECT
        industry,
        COUNT(*) AS comparable_sample_count,
        CAST(SUM(
            CASE
                WHEN presale_yoy > 20
                 AND revenue_yoy > 15
                THEN 1 ELSE 0
            END
        ) AS UNSIGNED) AS hit_count
    FROM scored
    GROUP BY industry
)
SELECT
    s.industry AS `行业`,
    s.hit_count AS `命中数`,
    s.comparable_sample_count AS `可比样本数`,
    ROUND(s.hit_count / NULLIF(s.comparable_sample_count, 0) * 100, 2) AS `命中占比`
FROM industry_stats s
ORDER BY `命中占比` DESC, `行业`;
