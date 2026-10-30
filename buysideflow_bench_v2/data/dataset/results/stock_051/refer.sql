WITH
params AS (
    SELECT
        DATE('2025-05-31') AS as_of_date,
        DATE('2024-12-31') AS latest_annual_end
),
trade_asof AS (
    SELECT MAX(t.TRADINGDATE) AS trade_date
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE < p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET IN (83, 90)
),
stock_seed AS (
    SELECT
        s.COMPANYCODE,
        MIN(s.INNERCODE) AS INNERCODE
    FROM secumain s
    JOIN params p
      ON 1 = 1
    JOIN trade_asof ta
      ON 1 = 1
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND s.LISTEDSECTOR = 1
      AND EXISTS (
          SELECT 1
          FROM lc_dindicesforvaluation v
          WHERE v.INNERCODE = s.INNERCODE
            AND v.TRADINGDAY = ta.trade_date
            AND v.TOTALMV IS NOT NULL
            AND v.PB IS NOT NULL
      )
      AND NOT EXISTS (
          SELECT 1
          FROM cs_riskalert ra
          WHERE ra.INNERCODE = s.INNERCODE
            AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
            AND DATE(ra.IMPLEMENTDATE) <= p.as_of_date
            AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
            AND (
                ra.REMOVEDATE IS NULL
                OR DATE(ra.REMOVEDATE) > p.as_of_date
                OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) >= DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
            )
      )
    GROUP BY s.COMPANYCODE
),
stock_base AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS company
    FROM stock_seed ss
    JOIN secumain s
      ON s.INNERCODE = ss.INNERCODE
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
                ORDER BY e.INFOPUBLDATE DESC, e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
        JOIN params p
          ON e.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(e.INSERTTIME, e.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
          AND e.FIRSTINDUSTRYNAME IN (
              '机械设备', '电力设备', '国防军工',
              '基础化工', '钢铁', '有色金属', '建筑材料', '轻工制造'
          )
    ) t
    WHERE rn = 1
),
candidate AS (
    SELECT
        sb.INNERCODE,
        sb.COMPANYCODE,
        sb.SECUCODE,
        sb.company,
        ia.FIRSTINDUSTRYNAME
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
),
balance_latest AS (
    SELECT
        t.COMPANYCODE,
        t.TOTALCURRENTASSETS,
        t.TOTALCURRENTLIABILITY,
        t.TOTALASSETS,
        t.TOTALLIABILITY
    FROM (
        SELECT
            b.COMPANYCODE,
            b.TOTALCURRENTASSETS,
            b.TOTALCURRENTLIABILITY,
            b.TOTALASSETS,
            b.TOTALLIABILITY,
            ROW_NUMBER() OVER (
                PARTITION BY b.COMPANYCODE
                ORDER BY b.INFOPUBLDATE DESC, b.JSID DESC
            ) AS rn
        FROM lc_balancesheetall b
        JOIN candidate c
          ON c.COMPANYCODE = b.COMPANYCODE
    JOIN params p
      ON b.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
     AND COALESCE(b.INSERTTIME, b.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE b.ENDDATE = p.latest_annual_end
          AND b.IFMERGED = 1
          AND b.IFADJUSTED = 2
          AND b.ACCOUNTINGSTANDARDS = 1
    ) t
    WHERE t.rn = 1
),
derivative_latest AS (
    SELECT
        t.COMPANYCODE,
        t.RETAINEDEARNINGS,
        t.EBIT
    FROM (
        SELECT
            f.COMPANYCODE,
            f.RETAINEDEARNINGS,
            f.EBIT,
            ROW_NUMBER() OVER (
                PARTITION BY f.COMPANYCODE
                ORDER BY CASE f.MARK
                             WHEN 1 THEN 0 WHEN 8 THEN 1 WHEN 7 THEN 2
                             WHEN 6 THEN 3 WHEN 2 THEN 4 ELSE 9
                         END,
                         f.INFOPUBLDATE DESC,
                         COALESCE(f.INSERTTIME, f.INFOPUBLDATE) DESC,
                         f.JSID DESC
            ) AS rn
        FROM fin_derivative f
        JOIN candidate c
          ON c.COMPANYCODE = f.COMPANYCODE
        JOIN params p
          ON f.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(f.INSERTTIME, f.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE f.MARK IN (1, 8, 7, 6, 2)
          AND f.ENDDATE = p.latest_annual_end
    ) t
    WHERE t.rn = 1
),
main_latest AS (
    SELECT
        t.COMPANYCODE,
        t.OPERATINGREENUE
    FROM (
        SELECT
            m.COMPANYCODE,
            m.OPERATINGREENUE,
            ROW_NUMBER() OVER (
                PARTITION BY m.COMPANYCODE
                ORDER BY CASE m.MARK
                             WHEN 1 THEN 0 WHEN 8 THEN 1 WHEN 7 THEN 2
                             WHEN 6 THEN 3 WHEN 2 THEN 4 ELSE 9
                         END,
                         m.INFOPUBLDATE DESC,
                         COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                         m.JSID DESC
            ) AS rn
        FROM lc_maindatanew m
        JOIN candidate c
          ON c.COMPANYCODE = m.COMPANYCODE
        JOIN params p
          ON m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
         AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
        WHERE m.MARK IN (1, 8, 7, 6, 2)
          AND m.ACCOUNTINGSTANDARDS = 1
          AND m.ENDDATE = p.latest_annual_end
    ) t
    WHERE t.rn = 1
),
valuation_key AS (
    SELECT
        v.INNERCODE,
        MAX(v.JSID) AS max_jsid
    FROM lc_dindicesforvaluation v
    JOIN trade_asof ta
      ON v.TRADINGDAY = ta.trade_date
    JOIN candidate c
      ON c.INNERCODE = v.INNERCODE
    WHERE v.TOTALMV IS NOT NULL
      AND v.PB IS NOT NULL
    GROUP BY v.INNERCODE
),
valuation_asof AS (
    SELECT
        v.INNERCODE,
        v.TOTALMV,
        v.PB
    FROM lc_dindicesforvaluation v
    JOIN valuation_key k
      ON k.INNERCODE = v.INNERCODE
     AND k.max_jsid = v.JSID
),
scored AS (
    SELECT
        c.SECUCODE AS code,
        c.company,
        c.FIRSTINDUSTRYNAME AS first_industry,
        1.2 * ((bl.TOTALCURRENTASSETS - bl.TOTALCURRENTLIABILITY) / NULLIF(bl.TOTALASSETS, 0)) +
        1.4 * (dl.RETAINEDEARNINGS / NULLIF(bl.TOTALASSETS, 0)) +
        3.3 * (dl.EBIT / NULLIF(bl.TOTALASSETS, 0)) +
        0.6 * (va.TOTALMV / NULLIF(bl.TOTALLIABILITY, 0)) +
        1.0 * (ml.OPERATINGREENUE / NULLIF(bl.TOTALASSETS, 0)) AS altman_z_score,
        va.PB AS pb
    FROM candidate c
    JOIN balance_latest bl
      ON bl.COMPANYCODE = c.COMPANYCODE
    JOIN derivative_latest dl
      ON dl.COMPANYCODE = c.COMPANYCODE
    JOIN main_latest ml
      ON ml.COMPANYCODE = c.COMPANYCODE
    JOIN valuation_asof va
      ON va.INNERCODE = c.INNERCODE
),
industry_total AS (
    SELECT
        first_industry,
        COUNT(*) AS industry_sample_count
    FROM scored
    GROUP BY first_industry
),
hit_detail AS (
    SELECT *
    FROM scored
    WHERE altman_z_score > 3
      AND pb < 1.5
),
ranked_hit AS (
    SELECT
        hd.*,
        ROW_NUMBER() OVER (
            PARTITION BY hd.first_industry
            ORDER BY hd.altman_z_score DESC, hd.pb ASC, hd.code ASC
        ) AS rn
    FROM hit_detail hd
),
industry_hit AS (
    SELECT
        first_industry,
        COUNT(*) AS hit_count
    FROM hit_detail
    GROUP BY first_industry
)
SELECT
    it.first_industry AS `行业`,
    COALESCE(ih.hit_count, 0) AS `入选数量`,
    it.industry_sample_count AS `样本数`,
    ROUND(COALESCE(ih.hit_count, 0) / NULLIF(it.industry_sample_count, 0) * 100, 2) AS `入选占比`,
    hd.code AS `股票代码`,
    hd.company AS `股票简称`,
    ROUND(hd.altman_z_score, 2) AS `Altman Z-Score`,
    ROUND(hd.pb, 2) AS `PB`
FROM industry_total it
LEFT JOIN industry_hit ih
  ON ih.first_industry = it.first_industry
LEFT JOIN ranked_hit hd
  ON hd.first_industry = it.first_industry
 AND hd.rn <= 3
ORDER BY `入选占比` DESC,
         `行业` ASC,
         hd.altman_z_score DESC,
         hd.pb ASC,
         hd.code ASC;
