WITH
params AS (
    SELECT
        DATE('2025-12-31') AS as_of_date,
        DATE('2025-09-30') AS current_end,
        DATE('2024-12-31') AS prior_end
),
a_share_candidates AS (
    SELECT
        sm.INNERCODE,
        sm.COMPANYCODE,
        sm.SECUCODE,
        COALESCE(sm.SECUABBR, sm.CHINAMEABBR, sm.CHINAME, sm.SECUCODE) AS secuabbr,
        sm.SECUMARKET,
        sm.LISTEDSECTOR
    FROM secumain sm
    JOIN params p
      ON sm.LISTEDDATE <= p.as_of_date
    WHERE sm.SECUCATEGORY = 1
      AND sm.SECUMARKET IN (83, 90)
      AND sm.LISTEDSECTOR IN (1, 2, 6, 7)
      AND sm.COMPANYCODE IS NOT NULL
),
latest_list_status AS (
    SELECT
        INNERCODE,
        CHANGETYPE
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
          ON ls.CHANGEDATE <= p.as_of_date
         AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) t
    WHERE rn = 1
),
stock_base AS (
    SELECT
        COMPANYCODE,
        INNERCODE,
        SECUCODE,
        secuabbr
    FROM (
        SELECT
            s.INNERCODE,
            s.COMPANYCODE,
            s.SECUCODE,
            s.secuabbr,
            ROW_NUMBER() OVER (
                PARTITION BY s.COMPANYCODE
                ORDER BY
                    CASE WHEN s.SECUMARKET = 83 THEN 1 ELSE 2 END,
                    CASE s.LISTEDSECTOR
                        WHEN 1 THEN 1
                        WHEN 2 THEN 2
                        WHEN 6 THEN 3
                        WHEN 7 THEN 4
                        ELSE 9
                    END,
                    s.SECUCODE
            ) AS rn
        FROM a_share_candidates s
        JOIN latest_list_status ls
          ON ls.INNERCODE = s.INNERCODE
         AND ls.CHANGETYPE IN (1, 3)
    ) t
    WHERE rn = 1
),
industry_asof AS (
    SELECT COMPANYCODE
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC,
                         COALESCE(e.CANCELDATE, DATE('9999-12-31')) DESC,
                         e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_date
        WHERE e.STANDARD = 37
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    ) t
    WHERE rn = 1
      AND FIRSTINDUSTRYNAME = '房地产'
),
debt_ranked AS (
    SELECT
        m.COMPANYCODE,
        m.ENDDATE,
        m.DEBTASSETSRATIO,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn
    FROM lc_mainindexnew m
    JOIN industry_asof ia
      ON ia.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.ENDDATE IN (p.current_end, p.prior_end)
     AND m.INFOPUBLDATE <= p.as_of_date
    WHERE m.DEBTASSETSRATIO IS NOT NULL
),
debt_pivot AS (
    SELECT
        COMPANYCODE,
        MAX(CASE WHEN ENDDATE = (SELECT current_end FROM params) THEN DEBTASSETSRATIO END) AS current_debt_ratio,
        MAX(CASE WHEN ENDDATE = (SELECT prior_end FROM params) THEN DEBTASSETSRATIO END) AS prior_debt_ratio
    FROM debt_ranked
    WHERE rn = 1
    GROUP BY COMPANYCODE
),
balance_ranked AS (
    SELECT
        b.COMPANYCODE,
        (
            COALESCE(b.BILLRECEIVABLE, 0) +
            COALESCE(b.ACCOUNTRECEIVABLE, 0) +
            COALESCE(b.OTHERRECEIVABLE, 0) +
            COALESCE(b.GOODWILL, 0) +
            COALESCE(b.INTANGIBLEASSETS, 0) +
            COALESCE(b.INVENTORIES, 0) +
            COALESCE(b.CONSTRUINPROCESS, 0)
        ) / NULLIF(b.TOTALASSETS, 0) AS risk_asset_ratio,
        ROW_NUMBER() OVER (
            PARTITION BY b.COMPANYCODE
            ORDER BY
                CASE WHEN b.IFADJUSTED = 2 THEN 1 ELSE 2 END,
                b.INFOPUBLDATE DESC,
                b.JSID DESC
        ) AS rn
    FROM lc_balancesheetall b
    JOIN industry_asof ia
      ON ia.COMPANYCODE = b.COMPANYCODE
    JOIN params p
      ON b.ENDDATE = p.current_end
     AND b.INFOPUBLDATE <= p.as_of_date
    WHERE b.IFMERGED = 1
      AND b.ACCOUNTINGSTANDARDS = 1
      AND b.TOTALASSETS > 0
),
st_filter AS (
    SELECT DISTINCT ra.INNERCODE
    FROM cs_riskalert ra
    JOIN params p
      ON ra.IMPLEMENTDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
     AND COALESCE(ra.IMPLANNOUCEDATE, ra.IMPLEMENTDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE ra.RISKALERTTYPE IN (1, 5, 12, 14)
      AND (
          ra.REMOVEDATE IS NULL
          OR ra.REMOVEDATE > p.as_of_date
          OR ra.REMOVEINFOPUBLDATE IS NULL
          OR ra.REMOVEINFOPUBLDATE >= DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      )
),
metric_universe AS (
    SELECT
        sb.INNERCODE,
        sb.SECUCODE,
        sb.secuabbr,
        dp.prior_debt_ratio,
        dp.current_debt_ratio,
        dp.current_debt_ratio - dp.prior_debt_ratio AS debt_change,
        br.risk_asset_ratio
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    JOIN debt_pivot dp
      ON dp.COMPANYCODE = sb.COMPANYCODE
    JOIN balance_ranked br
      ON br.COMPANYCODE = sb.COMPANYCODE
     AND br.rn = 1
    WHERE dp.current_debt_ratio IS NOT NULL
      AND dp.prior_debt_ratio IS NOT NULL
      AND br.risk_asset_ratio IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM st_filter sf WHERE sf.INNERCODE = sb.INNERCODE
      )
),
debt_ordered AS (
    SELECT
        current_debt_ratio,
        ROW_NUMBER() OVER (ORDER BY current_debt_ratio) AS rn,
        COUNT(*) OVER () AS n
    FROM metric_universe
),
industry_median AS (
    SELECT AVG(current_debt_ratio) AS median_current_debt_ratio
    FROM debt_ordered
    WHERE rn IN (FLOOR((n + 1) / 2), FLOOR((n + 2) / 2))
),
scored AS (
    SELECT
        mu.*,
        im.median_current_debt_ratio,
        PERCENT_RANK() OVER (ORDER BY mu.risk_asset_ratio) AS risk_asset_pct
    FROM metric_universe mu
    CROSS JOIN industry_median im
)
SELECT
    SECUCODE AS `股票代码`,
    secuabbr AS `股票简称`,
    ROUND(prior_debt_ratio, 2) AS `2024年报资产负债率`,
    ROUND(current_debt_ratio, 2) AS `2025年三季报资产负债率`,
    ROUND(debt_change, 2) AS `杠杆变化`,
    ROUND(risk_asset_ratio * 100, 2) AS `风险资产占比`
FROM scored
WHERE current_debt_ratio <= median_current_debt_ratio
  AND risk_asset_pct <= 0.80
  AND debt_change <= 5
ORDER BY current_debt_ratio ASC, risk_asset_ratio ASC, SECUCODE ASC;
