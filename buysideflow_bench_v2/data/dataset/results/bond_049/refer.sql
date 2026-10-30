WITH funding_ranked AS (
    SELECT
        DATE(ENDDATE) AS obs_date,
        FR007 - FR001 AS series_value,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(ENDDATE)
            ORDER BY UPDATETIME DESC, ID DESC
        ) AS rn
    FROM bond_baseratereference
    WHERE ENDDATE >= '2023-01-01'
      AND ENDDATE < '2025-01-01'
      AND FR007 IS NOT NULL
      AND FR001 IS NOT NULL
      AND (UPDATETIME IS NULL OR UPDATETIME < '2025-01-04')
),
index_quote_ranked AS (
    SELECT
        i.SECUCODE AS asset_id,
        DATE(q.TRADINGDAY) AS obs_date,
        q.CLOSEPRICE AS series_value,
        ROW_NUMBER() OVER (
            PARTITION BY i.SECUCODE, DATE(q.TRADINGDAY)
            ORDER BY q.UPDATETIME DESC, q.ID DESC
        ) AS rn
    FROM bond_chinabondindexquote q
    JOIN bond_indexbasicinfo i
      ON q.MAINCODE = i.INDEXCODE
    WHERE i.SECUCODE IN ('CBA00621', 'CBA00651')
      AND q.TRADINGDAY >= '2024-01-01'
      AND q.TRADINGDAY < '2025-04-01'
      AND q.CLOSEPRICE IS NOT NULL
      AND q.CLOSEPRICE > 0
      AND (q.UPDATETIME IS NULL OR q.UPDATETIME < DATE_ADD(DATE(q.TRADINGDAY), INTERVAL 1 DAY))
)
SELECT
    'funding_gap' AS series_name,
    CAST(NULL AS CHAR) AS asset_id,
    obs_date,
    series_value
FROM funding_ranked
WHERE rn = 1
UNION ALL
SELECT
    'index_price' AS series_name,
    asset_id,
    obs_date,
    series_value
FROM index_quote_ranked
WHERE rn = 1
ORDER BY series_name, asset_id, obs_date;
