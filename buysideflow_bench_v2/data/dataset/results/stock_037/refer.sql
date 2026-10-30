WITH
params AS (
    SELECT DATE('2025-11-17') AS as_of_date
),
target_index AS (
    SELECT
        s.INNERCODE,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS index_name
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE IN ('000016', '000300', '000905')
),
ranked_quotes AS (
    SELECT
        ti.INNERCODE,
        ti.SECUCODE,
        ti.index_name,
        q.CLOSEPRICE,
        q.TURNOVERVALUE,
        ROW_NUMBER() OVER (
            PARTITION BY ti.INNERCODE
            ORDER BY q.TRADINGDAY DESC
        ) AS rn_desc
    FROM target_index ti
    JOIN qt_indexquote q
      ON q.INNERCODE = ti.INNERCODE
    JOIN params p
      ON q.TRADINGDAY < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE q.CLOSEPRICE IS NOT NULL
      AND q.TURNOVERVALUE IS NOT NULL
),
window_80 AS (
    SELECT *
    FROM ranked_quotes
    WHERE rn_desc <= 80
),
metrics AS (
    SELECT
        INNERCODE,
        SECUCODE,
        index_name,
        AVG(CASE WHEN rn_desc BETWEEN 1 AND 60 THEN CLOSEPRICE END) AS current_ma60,
        AVG(CASE WHEN rn_desc BETWEEN 21 AND 80 THEN CLOSEPRICE END) AS prior_ma60,
        MAX(CASE WHEN rn_desc = 1 THEN CLOSEPRICE END) AS latest_close,
        AVG(CASE WHEN rn_desc BETWEEN 1 AND 10 THEN TURNOVERVALUE END) AS avg_turnover_10d,
        AVG(CASE WHEN rn_desc BETWEEN 11 AND 20 THEN TURNOVERVALUE END) AS avg_turnover_prior_10d,
        AVG(CASE WHEN rn_desc BETWEEN 1 AND 3 THEN TURNOVERVALUE END) AS avg_turnover_3d,
        AVG(CASE WHEN rn_desc BETWEEN 4 AND 10 THEN TURNOVERVALUE END) AS avg_turnover_prior_7d,
        COUNT(*) AS quote_count
    FROM window_80
    GROUP BY INNERCODE, SECUCODE, index_name
)
SELECT
    index_name AS `指数`,
    ROUND((current_ma60 / NULLIF(prior_ma60, 0) - 1) * 100, 2) AS `MA60变化`,
    ROUND((latest_close / NULLIF(current_ma60, 0) - 1) * 100, 2) AS `相对MA60偏离`,
    ROUND((avg_turnover_10d / NULLIF(avg_turnover_prior_10d, 0) - 1) * 100, 2) AS `10日成交额变化`,
    ROUND((avg_turnover_3d / NULLIF(avg_turnover_prior_7d, 0) - 1) * 100, 2) AS `3日成交额变化`
FROM metrics
WHERE quote_count = 80
ORDER BY FIELD(SECUCODE, '000016', '000300', '000905');
