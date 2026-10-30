WITH params AS (
    SELECT
        DATE('2024-12-31') AS as_of_date,
        DATE('2025-01-01') AS as_of_exclusive
),
valuation_day AS (
    SELECT
        v.ENDDATE AS valuation_date
    FROM params p
    JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
    WHERE v.ENDDATE <= p.as_of_date
      AND v.CREDIBILITYCODE = 1
      AND v.YIELDCODE = 2
      AND v.VPYIELD IS NOT NULL
      AND (v.INSERTTIME IS NULL OR v.INSERTTIME < p.as_of_exclusive)
      AND v.UPDATETIME < p.as_of_exclusive
    ORDER BY v.ENDDATE DESC
    LIMIT 1
),
valuation_candidates AS (
    SELECT
        bc.MAINCODE,
        bc.INNERCODE,
        bc.COMPANYCODE,
        bc.SECUCODE,
        bc.SECUABBR,
        v.VPYIELD,
        v.TRUEREMAINMATURITY,
        ROW_NUMBER() OVER (
            PARTITION BY bc.MAINCODE
            ORDER BY v.INSERTTIME DESC, v.ID DESC, bc.INNERCODE
        ) AS rn_main
    FROM params p
    CROSS JOIN valuation_day vd
    JOIN bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_N1)
      ON v.ENDDATE = vd.valuation_date
     AND v.CREDIBILITYCODE = 1
     AND v.YIELDCODE = 2
     AND v.VPYIELD IS NOT NULL
     AND v.TRUEREMAINMATURITY IS NOT NULL
     AND (v.INSERTTIME IS NULL OR v.INSERTTIME < p.as_of_exclusive)
     AND v.UPDATETIME < p.as_of_exclusive
    JOIN bond_code bc
      ON bc.INNERCODE = v.INNERCODE
    WHERE (bc.BONDTYPELEVEL1 IN (1700, 3700) OR bc.BONDNATURE IN (13, 36))
      AND (bc.INTERESTENDDATE IS NULL OR bc.INTERESTENDDATE >= vd.valuation_date)
      AND (bc.DELISTDATE IS NULL OR bc.DELISTDATE >= vd.valuation_date)
),
sample_bonds AS (
    SELECT
        MAINCODE,
        COMPANYCODE,
        VPYIELD,
        TRUEREMAINMATURITY
    FROM valuation_candidates
    WHERE rn_main = 1
),
latest_size_date AS (
    SELECT
        s.MAINCODE,
        MAX(bs.ENDDATE) AS size_date
    FROM sample_bonds s
    CROSS JOIN valuation_day vd
    JOIN bond_size bs
      ON bs.MAINCODE = s.MAINCODE
    WHERE bs.ENDDATE <= vd.valuation_date
      AND (bs.INFOPUBLDATE IS NULL OR bs.INFOPUBLDATE < DATE_ADD(DATE(vd.valuation_date), INTERVAL 1 DAY))
      AND bs.TOTALSIZE IS NOT NULL
    GROUP BY s.MAINCODE
),
latest_size AS (
    SELECT
        bs.MAINCODE,
        bs.TOTALSIZE / 100.0 AS balance_100m,
        ROW_NUMBER() OVER (
            PARTITION BY bs.MAINCODE
            ORDER BY bs.INFOPUBLDATE DESC, bs.UPDATETIME DESC, bs.ID DESC
        ) AS rn_size
    FROM latest_size_date lsd
    JOIN bond_size bs
      ON bs.MAINCODE = lsd.MAINCODE
     AND bs.ENDDATE = lsd.size_date
    CROSS JOIN valuation_day vd
    WHERE (bs.INFOPUBLDATE IS NULL OR bs.INFOPUBLDATE < DATE_ADD(DATE(vd.valuation_date), INTERVAL 1 DAY))
      AND bs.TOTALSIZE IS NOT NULL
),
industry_ranked AS (
    SELECT
        bi.COMPANYCODE,
        bi.FIRSTINDUSTRYNAME,
        ROW_NUMBER() OVER (
            PARTITION BY bi.COMPANYCODE
            ORDER BY bi.EFFECTIVEDATE DESC, bi.INFOPUBLDATE DESC, bi.ID DESC
        ) AS rn_industry
    FROM bond_biindustry bi
    CROSS JOIN valuation_day vd
    WHERE bi.STANDARD = 22
      AND bi.EFFECTIVEDATE <= vd.valuation_date
      AND bi.INFOPUBLDATE < DATE_ADD(DATE(vd.valuation_date), INTERVAL 1 DAY)
      AND (bi.CANCELDATE IS NULL OR bi.CANCELDATE > vd.valuation_date)
),
panel AS (
    SELECT
        COALESCE(ir.FIRSTINDUSTRYNAME, '未披露') AS industry_name,
        sb.MAINCODE,
        ls.balance_100m,
        sb.VPYIELD,
        sb.TRUEREMAINMATURITY
    FROM sample_bonds sb
    LEFT JOIN latest_size ls
      ON ls.MAINCODE = sb.MAINCODE
     AND ls.rn_size = 1
    LEFT JOIN industry_ranked ir
      ON ir.COMPANYCODE = sb.COMPANYCODE
     AND ir.rn_industry = 1
    WHERE ls.balance_100m IS NOT NULL
      AND ls.balance_100m > 0
),
ranked_yield AS (
    SELECT
        industry_name,
        MAINCODE,
        balance_100m,
        VPYIELD,
        TRUEREMAINMATURITY,
        ROW_NUMBER() OVER (
            PARTITION BY industry_name
            ORDER BY VPYIELD ASC, MAINCODE ASC
        ) AS rn_yield,
        COUNT(*) OVER (PARTITION BY industry_name) AS industry_cnt
    FROM panel
),
industry_stats AS (
    SELECT
        industry_name,
        COUNT(*) AS bond_cnt,
        SUM(balance_100m) AS total_balance_100m,
        AVG(CASE
            WHEN rn_yield IN (FLOOR((industry_cnt + 1) / 2), FLOOR((industry_cnt + 2) / 2))
            THEN VPYIELD
        END) AS median_yield_pct,
        SUM(balance_100m * TRUEREMAINMATURITY) / SUM(balance_100m) AS weighted_avg_remaining_years
    FROM ranked_yield
    GROUP BY industry_name
)
SELECT
    industry_name AS `行业`,
    bond_cnt AS `债券只数`,
    total_balance_100m AS `余额合计(亿元)`,
    median_yield_pct AS `估值收益率中位数(%)`,
    weighted_avg_remaining_years AS `余额加权平均剩余期限(年)`
FROM industry_stats
ORDER BY total_balance_100m DESC, industry_name ASC;
