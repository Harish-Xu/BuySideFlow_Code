WITH
params AS (
    SELECT
        DATE('2025-11-21') AS as_of_date,
        TIMESTAMP('2025-11-20 23:59:59') AS as_of_dt,
        DATE('2025-09-30') AS latest_financial_end
),
trade_asof AS (
    SELECT MAX(t.TRADINGDATE) AS trade_date
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE < p.as_of_date
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET IN (83, 90)
),
list_status_asof AS (
    SELECT
        INNERCODE,
        change_type
    FROM (
        SELECT
            ls.INNERCODE,
            ls.CHANGETYPE AS change_type,
            ROW_NUMBER() OVER (
                PARTITION BY ls.INNERCODE
                ORDER BY ls.CHANGEDATE DESC,
                         COALESCE(ls.INSERTTIME, ls.CHANGEDATE) DESC,
                         ls.JSID DESC
            ) AS rn
        FROM lc_liststatus ls
        JOIN params p
          ON ls.CHANGEDATE <= p.as_of_date
         AND COALESCE(ls.INSERTTIME, ls.CHANGEDATE) <= p.as_of_dt
        WHERE ls.SECUMARKET IN (83, 90)
          AND ls.CHANGETYPE BETWEEN 1 AND 9
    ) s
    WHERE rn = 1
),
stock_base AS (
    SELECT
        s.INNERCODE,
        s.COMPANYCODE,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS company
    FROM (
        SELECT
            sm.*,
            lsa.change_type,
            ROW_NUMBER() OVER (
                PARTITION BY sm.COMPANYCODE
                ORDER BY sm.SECUCODE, sm.INNERCODE
            ) AS rn_company
        FROM secumain sm
        LEFT JOIN list_status_asof lsa
          ON lsa.INNERCODE = sm.INNERCODE
        JOIN params p
          ON sm.LISTEDDATE <= p.as_of_date
        WHERE sm.SECUCATEGORY = 1
          AND sm.SECUMARKET IN (83, 90)
          AND sm.LISTEDSECTOR = 1
          AND lsa.change_type IN (1, 3)
    ) s
    WHERE s.rn_company = 1
),
industry_asof AS (
    SELECT
        COMPANYCODE,
        FIRSTINDUSTRYNAME AS industry_name
    FROM (
        SELECT
            e.COMPANYCODE,
            e.FIRSTINDUSTRYNAME,
            ROW_NUMBER() OVER (
                PARTITION BY e.COMPANYCODE
                ORDER BY e.INFOPUBLDATE DESC,
                         e.CANCELDATE DESC,
                         e.JSID DESC
            ) AS rn
        FROM lc_exgindustry e
        JOIN stock_base sb
          ON sb.COMPANYCODE = e.COMPANYCODE
        JOIN params p
          ON e.INFOPUBLDATE <= p.as_of_dt
         AND COALESCE(e.INSERTTIME, e.INFOPUBLDATE) <= p.as_of_dt
        WHERE e.STANDARD = 38
          AND (e.CANCELDATE IS NULL OR e.CANCELDATE > p.as_of_date)
    ) t
    WHERE t.rn = 1
),
fin_ranked AS (
    SELECT
        m.COMPANYCODE,
        m.ROETTM,
        m.OPERATINGREVENUEGROWRATE,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE
            ORDER BY m.INFOPUBLDATE DESC,
                     COALESCE(m.INSERTTIME, m.INFOPUBLDATE) DESC,
                     m.JSID DESC
        ) AS rn
    FROM lc_mainindexnew m
    JOIN stock_base sb
      ON sb.COMPANYCODE = m.COMPANYCODE
    JOIN params p
      ON m.ENDDATE = p.latest_financial_end
     AND m.INFOPUBLDATE <= p.as_of_dt
     AND COALESCE(m.INSERTTIME, m.INFOPUBLDATE) <= p.as_of_dt
    WHERE m.ROETTM IS NOT NULL
      AND m.OPERATINGREVENUEGROWRATE IS NOT NULL
),
latest_fin AS (
    SELECT
        COMPANYCODE,
        ROETTM,
        OPERATINGREVENUEGROWRATE
    FROM fin_ranked
    WHERE rn = 1
),
mv_ranked AS (
    SELECT
        v.INNERCODE,
        v.TOTALMV,
        ROW_NUMBER() OVER (
            PARTITION BY v.INNERCODE
            ORDER BY v.TRADINGDAY DESC, v.JSID DESC
        ) AS rn
    FROM lc_dindicesforvaluation v
    JOIN stock_base sb
      ON sb.INNERCODE = v.INNERCODE
    JOIN trade_asof ta
      ON v.TRADINGDAY = ta.trade_date
    WHERE v.TOTALMV IS NOT NULL
),
latest_mv AS (
    SELECT
        sb.COMPANYCODE,
        mv.TOTALMV
    FROM mv_ranked mv
    JOIN stock_base sb
      ON sb.INNERCODE = mv.INNERCODE
    WHERE mv.rn = 1
),
base AS (
    SELECT
        ia.industry_name,
        sb.COMPANYCODE,
        sb.SECUCODE,
        sb.company,
        lf.ROETTM,
        lf.OPERATINGREVENUEGROWRATE,
        mv.TOTALMV
    FROM stock_base sb
    JOIN industry_asof ia
      ON ia.COMPANYCODE = sb.COMPANYCODE
    JOIN latest_fin lf
      ON lf.COMPANYCODE = sb.COMPANYCODE
    JOIN latest_mv mv
      ON mv.COMPANYCODE = sb.COMPANYCODE
),
industry_stats AS (
    SELECT
        industry_name,
        COUNT(*) AS company_cnt,
        AVG(ROETTM) AS industry_avg_roe,
        AVG(OPERATINGREVENUEGROWRATE) AS industry_avg_revenue_growth
    FROM base
    GROUP BY industry_name
),
industry_scored AS (
    SELECT
        x.industry_name,
        x.industry_avg_roe,
        x.industry_avg_revenue_growth,
        (0.5 * x.roe_pct + 0.5 * x.revenue_growth_pct) AS composite_score
    FROM (
        SELECT
            s.industry_name,
            s.industry_avg_roe,
            s.industry_avg_revenue_growth,
            PERCENT_RANK() OVER (ORDER BY s.industry_avg_roe) AS roe_pct,
            PERCENT_RANK() OVER (ORDER BY s.industry_avg_revenue_growth) AS revenue_growth_pct
        FROM industry_stats s
    ) x
),
top_industries AS (
    SELECT
        industry_name,
        industry_avg_roe,
        industry_avg_revenue_growth,
        composite_score
    FROM (
        SELECT
            t.*,
            ROW_NUMBER() OVER (
                ORDER BY t.composite_score DESC, t.industry_avg_roe DESC, t.industry_name
            ) AS rn
        FROM industry_scored t
    ) x
    WHERE x.rn <= 3
),
company_ranked AS (
    SELECT
        ti.industry_name,
        ti.industry_avg_roe,
        ti.industry_avg_revenue_growth,
        ti.composite_score,
        b.SECUCODE,
        b.company,
        b.ROETTM,
        b.OPERATINGREVENUEGROWRATE,
        b.TOTALMV,
        ROW_NUMBER() OVER (
            PARTITION BY ti.industry_name
            ORDER BY b.TOTALMV DESC, b.SECUCODE
        ) AS mv_rank
    FROM top_industries ti
    JOIN base b
      ON b.industry_name = ti.industry_name
)
SELECT
    industry_name AS `行业`,
    ROUND(industry_avg_roe, 2) AS `行业平均ROE`,
    ROUND(industry_avg_revenue_growth, 2) AS `行业平均营收增速`,
    ROUND(composite_score, 6) AS `综合得分`,
    SECUCODE AS `股票代码`,
    company AS `公司简称`,
    ROUND(TOTALMV / 100000000, 2) AS `总市值(亿元)`
FROM company_ranked
WHERE mv_rank <= 2
ORDER BY `综合得分` DESC, `行业`, `总市值(亿元)` DESC, `股票代码`;
