WITH params AS (
    SELECT TIMESTAMP('2025-05-01 00:00:00') AS next_day_ts
),
financial_ranked AS (
    SELECT
        m.COMPANYCODE,
        m.ENDDATE AS report_period,
        m.NETOPERATECASHFLOW AS operating_cash_flow,
        m.TOTALLIABILITY AS total_liability,
        m.OPERATINGREENUE AS operating_revenue,
        ROW_NUMBER() OVER (
            PARTITION BY m.COMPANYCODE, m.ENDDATE
            ORDER BY
                CASE m.MARK
                    WHEN 1 THEN 1 WHEN 8 THEN 2 WHEN 7 THEN 3
                    WHEN 6 THEN 4 WHEN 2 THEN 5 ELSE 99
                END,
                m.INFOPUBLDATE DESC,
                m.ID DESC
        ) AS rn
    FROM lc_maindatanew m
    CROSS JOIN params p
    WHERE m.ENDDATE IN (DATE('2024-03-31'), DATE('2025-03-31'))
      AND m.INFOPUBLDATE < p.next_day_ts
),
q1_signal AS (
    SELECT
        c.COMPANYCODE,
        c.operating_cash_flow,
        (c.total_liability / py.total_liability - 1) * 100 AS liability_yoy_pct,
        (c.operating_revenue / py.operating_revenue - 1) * 100 AS revenue_yoy_pct
    FROM financial_ranked c
    JOIN financial_ranked py
      ON py.COMPANYCODE = c.COMPANYCODE
     AND py.report_period = DATE('2024-03-31')
     AND py.rn = 1
    WHERE c.report_period = DATE('2025-03-31')
      AND c.rn = 1
      AND c.operating_cash_flow < 0
      AND py.operating_cash_flow > 0
      AND c.total_liability IS NOT NULL
      AND c.operating_revenue IS NOT NULL
      AND py.total_liability > 0
      AND py.operating_revenue > 0
      AND c.total_liability / py.total_liability
          > c.operating_revenue / py.operating_revenue
),
active_code AS (
    SELECT
        bc.MAINCODE,
        bc.COMPANYCODE,
        bc.ISSUER,
        ROW_NUMBER() OVER (
            PARTITION BY bc.MAINCODE
            ORDER BY
                CASE WHEN bc.SECUMARKET = 89 THEN 0 ELSE 1 END,
                COALESCE(bc.SECUCODE, ''),
                bc.INNERCODE
        ) AS rn
    FROM bond_code bc
    JOIN q1_signal q ON q.COMPANYCODE = bc.COMPANYCODE
    CROSS JOIN params p
    WHERE bc.MAINCODE IS NOT NULL
      AND bc.ISSUER IS NOT NULL
      AND (
            bc.BONDTYPELEVEL1 IN (1600, 1700, 1800, 2500, 2600, 2800, 2900, 3100, 3200, 3300, 3700)
         OR bc.BONDTYPELEVEL2 IN (1901, 1903)
      )
      AND (bc.INTERESTENDDATE IS NULL OR bc.INTERESTENDDATE >= p.next_day_ts)
),
active_bonds AS (
    SELECT MAINCODE, COMPANYCODE, ISSUER
    FROM active_code
    WHERE rn = 1
),
size_ranked AS (
    SELECT
        a.COMPANYCODE,
        a.ISSUER,
        a.MAINCODE,
        s.TOTALSIZE,
        ROW_NUMBER() OVER (
            PARTITION BY a.MAINCODE
            ORDER BY s.ENDDATE DESC, s.INFOPUBLDATE DESC, s.ID DESC
        ) AS rn
    FROM active_bonds a
    JOIN bond_size s ON s.MAINCODE = a.MAINCODE
    CROSS JOIN params p
    WHERE s.ENDDATE < p.next_day_ts
      AND s.INFOPUBLDATE < p.next_day_ts
),
issuer_balance AS (
    SELECT
        COMPANYCODE,
        MIN(ISSUER) AS issuer,
        SUM(TOTALSIZE) / 100.0 AS outstanding_balance_100m
    FROM size_ranked
    WHERE rn = 1 AND TOTALSIZE > 0
    GROUP BY COMPANYCODE
    HAVING SUM(TOTALSIZE) / 100.0 >= 200
)
SELECT
    ib.issuer AS `发行人`,
    ROUND(q.operating_cash_flow / 100000000.0, 2) AS `经营活动现金流净额`,
    ROUND(q.liability_yoy_pct, 2) AS `总负债同比增速`,
    ROUND(q.revenue_yoy_pct, 2) AS `营业收入同比增速`,
    ROUND(ib.outstanding_balance_100m, 2) AS `存续债余额`
FROM q1_signal q
JOIN issuer_balance ib ON ib.COMPANYCODE = q.COMPANYCODE
ORDER BY `存续债余额` DESC, `总负债同比增速` DESC, `发行人`;
