WITH fund_base AS (
    SELECT f.INNERCODE, f.SECURITYCODE, f.ESTABLISHMENTDATE
    FROM mf_fundarchives f
    WHERE f.INVESTMENTTYPE = 16
      AND COALESCE(f.IFFOF, 2) <> 1
      AND f.ESTABLISHMENTDATE >= DATE_SUB('2025-10-31', INTERVAL 6 DAY)
      AND f.ESTABLISHMENTDATE <= '2025-10-31'
),
latest_type AS (
    SELECT
        t.INNERCODE,
        t.SECASSETCATCODE,
        ROW_NUMBER() OVER (
            PARTITION BY t.INNERCODE
            ORDER BY t.EFFECTIVEDATE DESC, t.INSERTTIME DESC, t.ID DESC
        ) AS rn
    FROM mf_jyfundtype t
    JOIN fund_base f ON f.INNERCODE = t.INNERCODE
    WHERE t.STANDARD = 75
      AND t.EFFECTIVEDATE <= DATE('2025-10-31')
      AND (t.CANCELDATE IS NULL OR t.CANCELDATE > DATE('2025-10-31'))
      AND t.INSERTTIME < DATE_ADD(DATE('2025-10-31'), INTERVAL 1 DAY)
),
eligible AS (
    SELECT f.*
    FROM fund_base f
    JOIN latest_type lt ON lt.INNERCODE = f.INNERCODE AND lt.rn = 1
    WHERE lt.SECASSETCATCODE IN (1101, 1201, 1202, 1203)
),
short_name AS (
    SELECT x.INNERCODE, x.DISCLNAME
    FROM (
        SELECT p.INNERCODE, p.DISCLNAME,
            ROW_NUMBER() OVER (
                PARTITION BY p.INNERCODE
                ORDER BY p.INFOPUBLDATE DESC, p.EFFECTIVEDATE DESC, p.ID DESC
            ) AS rn
        FROM mf_fundprodname p
        JOIN eligible f ON f.INNERCODE = p.INNERCODE
        WHERE p.INFOTYPE = 6
          AND p.EFFECTIVEDATE <= '2025-10-31'
          AND (p.EXPIRYDATE IS NULL OR p.EXPIRYDATE > '2025-10-31')
          AND p.INFOPUBLDATE < DATE_ADD(DATE('2025-10-31'), INTERVAL 1 DAY)
    ) x
    WHERE x.rn = 1
),
issue_amount AS (
    SELECT
        x.INNERCODE,
        x.ACTRAISINGAMOUNT
    FROM (
        SELECT
            il.INNERCODE,
            il.ACTRAISINGAMOUNT,
            ROW_NUMBER() OVER (
                PARTITION BY il.INNERCODE
                ORDER BY
                    COALESCE(il.INITIALINFOPUBLDATE, il.INSERTTIME) DESC,
                    il.INSERTTIME DESC,
                    il.ID DESC
            ) AS rn
        FROM mf_issueandlisting il
        JOIN eligible f ON f.INNERCODE = il.INNERCODE
        WHERE (il.FUNDRAISINGMETHOD = 1 OR il.FUNDRAISINGMETHOD IS NULL)
          AND COALESCE(il.INITIALINFOPUBLDATE, il.INSERTTIME)
              < DATE_ADD(DATE('2025-10-31'), INTERVAL 1 DAY)
    ) x
    WHERE x.rn = 1
),
active_equity AS (
    SELECT
        f.INNERCODE,
        f.SECURITYCODE AS fund_code,
        COALESCE(sm.SECUABBR, sn.DISCLNAME) AS fund_name,
        f.ESTABLISHMENTDATE,
        ia.ACTRAISINGAMOUNT AS raising_amount
    FROM eligible f
    LEFT JOIN secumain sm
        ON sm.INNERCODE = f.INNERCODE
    LEFT JOIN short_name sn ON sn.INNERCODE = f.INNERCODE
    LEFT JOIN issue_amount ia
        ON ia.INNERCODE = f.INNERCODE
)
SELECT
    ae.fund_code AS `基金代码`,
    ae.fund_name AS `基金简称`,
    ae.ESTABLISHMENTDATE AS `成立日期`,
    ROUND(ae.raising_amount / 10000, 2) AS `设立规模（万元）`
FROM active_equity ae
ORDER BY ae.raising_amount DESC, ae.fund_code;
