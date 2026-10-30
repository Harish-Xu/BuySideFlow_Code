WITH params AS (
    SELECT DATE('2025-08-30') AS asof_day
),
share_universe AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE,
        COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) AS main_fund_code,
        COALESCE(sm.CHINAMEABBR, sm.SECUABBR, sm.CHINAME, fa.SECURITYCODE) AS fund_name,
        ROW_NUMBER() OVER (
            PARTITION BY COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE)
            ORDER BY
                CASE
                    WHEN fa.SECURITYCODE = COALESCE(NULLIF(fa.MAINCODE, ''), fa.SECURITYCODE) THEN 0
                    ELSE 1
                END,
                fa.SECURITYCODE ASC,
                fa.INNERCODE ASC
        ) AS rn_main
    FROM mf_fundarchives fa
    JOIN secumain sm
      ON sm.INNERCODE = fa.INNERCODE
     AND sm.SECUCATEGORY = 8
    JOIN params p
      ON 1 = 1
    WHERE fa.INVESTMENTTYPE = 16
      AND fa.TYPE NOT IN (4, 8)
      AND COALESCE(sm.SECUABBR, '') NOT LIKE '%联接%'
      AND COALESCE(sm.CHINAMEABBR, '') NOT LIKE '%联接%'
      AND COALESCE(sm.CHINAME, '') NOT LIKE '%联接%'
      AND fa.ESTABLISHMENTDATE <= p.asof_day
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.asof_day)
),
main_fund_universe AS (
    SELECT
        INNERCODE,
        SECURITYCODE AS fund_code,
        main_fund_code,
        fund_name
    FROM share_universe
    WHERE rn_main = 1
),
quarter_dates AS (
    SELECT q.ENDDATE
    FROM (
        SELECT DISTINCT sa.ENDDATE
        FROM mf_scaleanalysis sa
        JOIN params p
          ON 1 = 1
        WHERE sa.ENDDATE <= p.asof_day
          AND sa.INSERTTIME < DATE_ADD(p.asof_day, INTERVAL 1 DAY)
          AND sa.COMBNVI IS NOT NULL
          AND DATE_FORMAT(sa.ENDDATE, '%m-%d') IN ('03-31', '06-30', '09-30', '12-31')
    ) q
    ORDER BY q.ENDDATE DESC
    LIMIT 4
),
ordered_quarters AS (
    SELECT
        q.ENDDATE,
        ROW_NUMBER() OVER (ORDER BY q.ENDDATE ASC) AS quarter_seq
    FROM quarter_dates q
),
scale_asof AS (
    SELECT
        sa.INNERCODE,
        sa.ENDDATE,
        sa.COMBNVI AS combined_scale,
        ROW_NUMBER() OVER (
            PARTITION BY sa.INNERCODE, sa.ENDDATE
            ORDER BY sa.INSERTTIME DESC, sa.ID DESC, sa.JSID DESC
        ) AS rn
    FROM mf_scaleanalysis sa
    JOIN params p
      ON 1 = 1
    WHERE sa.ENDDATE <= p.asof_day
      AND sa.INSERTTIME < DATE_ADD(p.asof_day, INTERVAL 1 DAY)
      AND sa.COMBNVI IS NOT NULL
),
fund_quarter_scale AS (
    SELECT
        mfu.fund_code,
        mfu.fund_name,
        oq.quarter_seq,
        oq.ENDDATE,
        sa.combined_scale
    FROM main_fund_universe mfu
    JOIN ordered_quarters oq
      ON 1 = 1
    JOIN scale_asof sa
      ON sa.INNERCODE = mfu.INNERCODE
     AND sa.ENDDATE = oq.ENDDATE
     AND sa.rn = 1
),
pivoted AS (
    SELECT
        fund_code,
        fund_name,
        MAX(CASE WHEN quarter_seq = 1 THEN ENDDATE END) AS date_q1,
        MAX(CASE WHEN quarter_seq = 1 THEN combined_scale END) AS scale_q1,
        MAX(CASE WHEN quarter_seq = 2 THEN ENDDATE END) AS date_q2,
        MAX(CASE WHEN quarter_seq = 2 THEN combined_scale END) AS scale_q2,
        MAX(CASE WHEN quarter_seq = 3 THEN ENDDATE END) AS date_q3,
        MAX(CASE WHEN quarter_seq = 3 THEN combined_scale END) AS scale_q3,
        MAX(CASE WHEN quarter_seq = 4 THEN ENDDATE END) AS date_q4,
        MAX(CASE WHEN quarter_seq = 4 THEN combined_scale END) AS scale_q4
    FROM fund_quarter_scale
    GROUP BY fund_code, fund_name
),
qualified_funds AS (
    SELECT
        fund_code,
        fund_name,
        date_q1,
        scale_q1,
        date_q2,
        scale_q2,
        date_q3,
        scale_q3,
        date_q4,
        scale_q4
    FROM pivoted
    WHERE scale_q1 IS NOT NULL
      AND scale_q2 IS NOT NULL
      AND scale_q3 IS NOT NULL
      AND scale_q4 IS NOT NULL
      AND scale_q2 > scale_q1
      AND scale_q3 > scale_q2
      AND scale_q4 > scale_q3
      AND scale_q4 BETWEEN 3000000000 AND 5000000000
)
SELECT
    fund_code AS 主基金代码,
    fund_name AS 基金简称,
    DATE_FORMAT(date_q1, '%Y-%m-%d') AS 季末日期1,
    ROUND(scale_q1 / 100000000, 2) AS 季末规模1_亿元,
    DATE_FORMAT(date_q2, '%Y-%m-%d') AS 季末日期2,
    ROUND(scale_q2 / 100000000, 2) AS 季末规模2_亿元,
    DATE_FORMAT(date_q3, '%Y-%m-%d') AS 季末日期3,
    ROUND(scale_q3 / 100000000, 2) AS 季末规模3_亿元,
    DATE_FORMAT(date_q4, '%Y-%m-%d') AS 季末日期4,
    ROUND(scale_q4 / 100000000, 2) AS 季末规模4_亿元
FROM qualified_funds
ORDER BY scale_q4 DESC, fund_code ASC
LIMIT 10;
