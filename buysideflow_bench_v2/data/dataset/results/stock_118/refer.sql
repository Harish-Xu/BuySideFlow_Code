WITH params AS (
    SELECT DATE('2023-06-21') AS as_of_date
),
ranked_alerts AS (
    SELECT
        s.SECUCODE,
        COALESCE(NULLIF(s.SECUABBR, ''), NULLIF(s.CHINAMEABBR, ''), s.CHINAME) AS stock_name,
        ra.IMPLEMENTDATE,
        ra.IMPLANNOUCEDATE,
        ROW_NUMBER() OVER (
            PARTITION BY s.INNERCODE
            ORDER BY
                ra.IMPLEMENTDATE DESC,
                ra.IMPLANNOUCEDATE DESC,
                ra.INSERTTIME DESC,
                ra.JSID DESC,
                ra.ID DESC
        ) AS rn
    FROM secumain s
    JOIN qt_dailyquote q
      ON q.INNERCODE = s.INNERCODE
    JOIN cs_riskalert ra
      ON ra.INNERCODE = s.INNERCODE
    JOIN params p
      ON q.TRADINGDAY = p.as_of_date
    WHERE s.SECUCATEGORY = 1
      AND s.SECUMARKET IN (83, 90)
      AND ra.RISKALERTTYPE = 14
      AND ra.IMPLEMENTDATE <= p.as_of_date
      AND ra.IMPLANNOUCEDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      AND (
          ra.REMOVEDATE IS NULL
          OR ra.REMOVEDATE > p.as_of_date
          OR ra.REMOVEINFOPUBLDATE >= DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
      )
)
SELECT
    LPAD(SECUCODE, 6, '0') AS `股票代码`,
    stock_name AS `股票简称`,
    DATE(IMPLEMENTDATE) AS `实施日期`,
    DATE(IMPLANNOUCEDATE) AS `实施公告日期`
FROM ranked_alerts
WHERE rn = 1
ORDER BY SECUCODE ASC;
