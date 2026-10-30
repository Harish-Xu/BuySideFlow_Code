WITH valuation_rows AS (
    SELECT
        MAINCODE,
        INNERCODE,
        SECUMARKET,
        SECUCODE,
        SECUABBR,
        VPYIELD,
        TRUEYIELD
    FROM (
        SELECT
            bc.MAINCODE,
            bc.INNERCODE,
            bc.SECUMARKET,
            bc.SECUCODE,
            bc.SECUABBR,
            v.VPYIELD,
            v.TRUEYIELD,
            ROW_NUMBER() OVER (
                PARTITION BY bc.INNERCODE
                ORDER BY v.UPDATETIME DESC, v.INSERTTIME DESC, v.ID DESC
            ) AS rn
        FROM bond_cbvaluationall v FORCE INDEX (IX_BOND_CBVALUATIONALL_Q047)
        JOIN bond_code bc
          ON v.INNERCODE = bc.INNERCODE
        WHERE v.ENDDATE >= DATE('2024-12-31')
          AND v.ENDDATE < DATE_ADD(DATE('2024-12-31'), INTERVAL 1 DAY)
          AND v.CREDIBILITYCODE = 1
          AND v.YIELDCODE = 2
          AND v.VPYIELD IS NOT NULL
          AND v.TRUEYIELD IS NOT NULL
          AND (v.INSERTTIME IS NULL OR v.INSERTTIME < DATE_ADD(DATE('2024-12-31'), INTERVAL 1 DAY))
          AND v.UPDATETIME < DATE_ADD(DATE('2024-12-31'), INTERVAL 1 DAY)
          AND v.TRUEREMAINMATURITY > 1
          AND v.TRUEREMAINMATURITY <= 5
          AND bc.MAINCODE IS NOT NULL
          AND bc.INNERCODE IS NOT NULL
          AND bc.SECUMARKET IS NOT NULL
          AND bc.SECUCODE IS NOT NULL
          AND bc.SECUABBR IS NOT NULL
          AND (
                bc.BONDTYPELEVEL1 IN (1600,1700,1800,2500,2600,2800,2900,3100,3200,3300,3700)
             OR bc.BONDTYPELEVEL2 IN (1901,1903)
          )
          AND (bc.LISTEDDATE IS NULL OR DATE(bc.LISTEDDATE) <= DATE('2024-12-31'))
          AND (bc.INTERESTENDDATE IS NULL OR DATE(bc.INTERESTENDDATE) > DATE('2024-12-31'))
          AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) > DATE('2024-12-31'))
    ) x
    WHERE rn = 1
),
latest_rating AS (
    SELECT MAINCODE, CRDESC
    FROM (
        SELECT
            MAINCODE,
            CRDESC,
            ROW_NUMBER() OVER (
                PARTITION BY MAINCODE
                ORDER BY INFOPUBLDATE DESC, CRDATE DESC, UPDATETIME DESC, ID DESC
            ) AS rn
        FROM bond_bdcreditgrading
        WHERE INFOPUBLDATE < DATE_ADD(DATE('2024-12-31'), INTERVAL 1 DAY)
          AND CRTYPECODE = 2
          AND CRSYSTEM = 1
          AND CRSTATUS = 1
          AND CRDESC IS NOT NULL
          AND (INSERTTIME IS NULL OR INSERTTIME < DATE_ADD(DATE('2024-12-31'), INTERVAL 1 DAY))
          AND UPDATETIME < DATE_ADD(DATE('2024-12-31'), INTERVAL 1 DAY)
          AND (
              RATINGEFFECTIVEENDDATE IS NULL
              OR RATINGEFFECTIVEENDDATE >= DATE('2024-12-31')
          )
    ) r
    WHERE rn = 1
),
turnover AS (
    SELECT
        q.INNERCODE,
        q.SECUMARKET,
        SUM(q.TURNOVERVALUE) AS turnover_yuan
    FROM valuation_rows v
    JOIN bond_basicquote q FORCE INDEX (IX_BOND_BASICQUOTE)
      ON q.INNERCODE = v.INNERCODE
     AND q.SECUMARKET = v.SECUMARKET
    WHERE q.TRADINGDAY >= DATE('2024-12-31')
      AND q.TRADINGDAY < DATE_ADD(DATE('2024-12-31'), INTERVAL 1 DAY)
      AND q.TURNOVERVALUE IS NOT NULL
      AND q.TURNOVERVALUE > 0
      AND q.INSERTTIME < DATE_ADD(DATE('2024-12-31'), INTERVAL 1 DAY)
    GROUP BY q.INNERCODE, q.SECUMARKET
),
traded AS (
    SELECT
        v.MAINCODE,
        v.SECUCODE,
        v.SECUABBR,
        v.TRUEYIELD,
        v.VPYIELD,
        t.turnover_yuan
    FROM valuation_rows v
    JOIN latest_rating r
      ON v.MAINCODE = r.MAINCODE
     AND r.CRDESC IN ('AAA', 'AA+')
    JOIN turnover t
      ON v.INNERCODE = t.INNERCODE
     AND v.SECUMARKET = t.SECUMARKET
),
bond_agg AS (
    SELECT
        MAINCODE,
        SUM(turnover_yuan) AS turnover_yuan,
        SUM(TRUEYIELD * turnover_yuan) / SUM(turnover_yuan) AS trade_yield_pct,
        SUM(VPYIELD * turnover_yuan) / SUM(turnover_yuan) AS valuation_yield_pct
    FROM traded
    GROUP BY MAINCODE
),
representative AS (
    SELECT MAINCODE, SECUCODE, SECUABBR
    FROM (
        SELECT
            MAINCODE,
            SECUCODE,
            SECUABBR,
            ROW_NUMBER() OVER (
                PARTITION BY MAINCODE
                ORDER BY turnover_yuan DESC, SECUCODE ASC
            ) AS rn
        FROM traded
    ) x
    WHERE rn = 1
),
ranked AS (
    SELECT
        a.MAINCODE,
        r.SECUCODE,
        r.SECUABBR,
        a.turnover_yuan,
        a.trade_yield_pct,
        a.valuation_yield_pct,
        (a.trade_yield_pct - a.valuation_yield_pct) * 100 AS deviation_bp,
        ROW_NUMBER() OVER (ORDER BY a.turnover_yuan DESC, r.SECUCODE ASC) AS turnover_rn,
        COUNT(*) OVER () AS turnover_cnt
    FROM bond_agg a
    JOIN representative r
      ON a.MAINCODE = r.MAINCODE
)
SELECT
    SECUCODE AS `债券代码`,
    SECUABBR AS `债券简称`,
    ROUND(turnover_yuan, 2) AS `成交额`,
    ROUND(trade_yield_pct, 4) AS `成交收益率`,
    ROUND(valuation_yield_pct, 4) AS `估值收益率`,
    ROUND(deviation_bp, 2) AS `偏离值`
FROM ranked
WHERE turnover_rn <= 10
  AND deviation_bp > 2
ORDER BY deviation_bp DESC, turnover_yuan DESC, SECUCODE ASC;
