WITH periods AS (
    SELECT DATE('2025-09-30') AS period_end
    UNION ALL SELECT DATE('2025-12-31')
),
idx AS (
    SELECT INNERCODE AS index_innercode, SECUCODE, SECUABBR
    FROM secumain
    WHERE SECUCODE IN ('000010', '000698') AND SECUMARKET = 83
),
relations AS (
    SELECT p.period_end, fa.INNERCODE, mt.TRACEDINDEXCODE,
           ROW_NUMBER() OVER (
               PARTITION BY p.period_end, fa.INNERCODE
               ORDER BY mt.EXECUTEDATE DESC, mt.INFOPUBLDATE DESC, mt.ID DESC
           ) AS rn
    FROM periods p
    JOIN mf_fundarchives fa
      ON fa.TYPE = 4 AND fa.MAINCODE = fa.SECURITYCODE
     AND fa.ESTABLISHMENTDATE <= p.period_end
     AND fa.LISTEDDATE <= p.period_end
     AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE > p.period_end)
    JOIN secumain sm ON sm.INNERCODE = fa.INNERCODE AND sm.SECUCATEGORY = 8
    JOIN mf_investtargetcriterion mt
      ON mt.INNERCODE = fa.INNERCODE AND mt.INVESTTARGET = '7'
     AND mt.EXECUTEDATE <= p.period_end
     AND mt.INFOPUBLDATE < DATE('2026-01-24')
     AND (mt.CANCELDATE IS NULL OR mt.CANCELDATE > p.period_end)
),
pool AS (
    SELECT r.period_end, r.INNERCODE, r.TRACEDINDEXCODE
    FROM relations r
    JOIN idx i ON i.index_innercode = r.TRACEDINDEXCODE
    WHERE r.rn = 1
),
scale_ranked AS (
    SELECT s.INNERCODE, s.ENDDATE, s.COMBNVI, s.COMBTOTALSHARES,
           ROW_NUMBER() OVER (
               PARTITION BY s.INNERCODE, s.ENDDATE
               ORDER BY s.INSERTTIME DESC, s.ID DESC
           ) AS rn
    FROM mf_scaleanalysis s
    JOIN (SELECT DISTINCT INNERCODE FROM pool) f ON f.INNERCODE = s.INNERCODE
    WHERE s.ENDDATE IN ('2025-09-30', '2025-12-31')
      AND s.INSERTTIME < DATE('2026-01-24')
),
totals AS (
    SELECT p.period_end, p.TRACEDINDEXCODE,
           CASE WHEN COUNT(s.COMBNVI) = COUNT(*) THEN SUM(s.COMBNVI) END AS scale_yuan,
           CASE WHEN COUNT(s.COMBTOTALSHARES) = COUNT(*) THEN SUM(s.COMBTOTALSHARES) END AS shares
    FROM pool p
    LEFT JOIN scale_ranked s
      ON s.INNERCODE = p.INNERCODE AND s.ENDDATE = p.period_end AND s.rn = 1
    GROUP BY p.period_end, p.TRACEDINDEXCODE
)
SELECT CONCAT(i.SECUCODE, '.SH') AS `指数代码`,
       i.SECUABBR AS `指数简称`,
       ROUND(cur.scale_yuan / 100000000, 2) AS `年末规模(亿元)`,
       ROUND(cur.shares / 100000000, 2) AS `年末份额`,
       ROUND((cur.scale_yuan / NULLIF(prev.scale_yuan, 0) - 1) * 100, 2) AS `规模环比`,
       ROUND((cur.shares / NULLIF(prev.shares, 0) - 1) * 100, 2) AS `份额环比`
FROM idx i
LEFT JOIN totals cur
  ON cur.TRACEDINDEXCODE = i.index_innercode AND cur.period_end = '2025-12-31'
LEFT JOIN totals prev
  ON prev.TRACEDINDEXCODE = i.index_innercode AND prev.period_end = '2025-09-30'
ORDER BY i.SECUCODE;
