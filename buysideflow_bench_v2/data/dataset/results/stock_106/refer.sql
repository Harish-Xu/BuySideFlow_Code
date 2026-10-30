WITH params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
pmi_ranked AS (
    SELECT
        m.ENDDATE,
        m.INFOPUBLDATE,
        m.DATAVALUE,
        ROW_NUMBER() OVER (
            PARTITION BY m.INDICATORCODE, m.ENDDATE
            ORDER BY m.INFOPUBLDATE DESC, m.ID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    JOIN params p
      ON m.ENDDATE <= p.as_of_date
     AND m.INFOPUBLDATE < DATE_ADD(p.as_of_date, INTERVAL 1 DAY)
    WHERE m.INDICATORCODE = 110166523
      AND m.DATAVALUE IS NOT NULL
      AND m.ENDDATE >= DATE_SUB(p.as_of_date, INTERVAL 30 MONTH)
),
pmi_data AS (
    SELECT
        'pmi' AS data_type,
        CAST(NULL AS CHAR) AS instrument_code,
        CAST(NULL AS CHAR) AS instrument_name,
        m.ENDDATE AS data_date,
        m.INFOPUBLDATE AS info_publish_date,
        m.DATAVALUE AS data_value,
        CAST(NULL AS DECIMAL(19, 8)) AS close_price,
        CAST(NULL AS DECIMAL(19, 8)) AS daily_return
    FROM pmi_ranked m
    WHERE m.rn = 1
),
trade_days AS (
    SELECT DISTINCT t.TRADINGDATE
    FROM qt_tradingdaynew t
    JOIN params p
      ON t.TRADINGDATE <= p.as_of_date
     AND t.TRADINGDATE >= DATE('2024-01-01')
    WHERE t.IFTRADINGDAY = 1
      AND t.SECUMARKET = 83
),
calendar_data AS (
    SELECT
        'calendar' AS data_type,
        CAST(NULL AS CHAR) AS instrument_code,
        CAST(NULL AS CHAR) AS instrument_name,
        td.TRADINGDATE AS data_date,
        CAST(NULL AS DATETIME) AS info_publish_date,
        CAST(NULL AS DECIMAL(19, 8)) AS data_value,
        CAST(NULL AS DECIMAL(19, 8)) AS close_price,
        CAST(NULL AS DECIMAL(19, 8)) AS daily_return
    FROM trade_days td
),
target_indices AS (
    SELECT
        libi.INDEXCODE AS innercode,
        sm.SECUCODE AS instrument_code,
        REPLACE(sm.SECUABBR, '申万', '') AS instrument_name
    FROM lc_indexbasicinfo libi
    JOIN secumain sm
      ON sm.INNERCODE = libi.INDEXCODE
    WHERE libi.PUBINDEXTYPE = 121101
      AND libi.INDUSTRYSTANDARD = 38
      AND sm.SECUCODE IN ('801030', '801040', '801050', '801890')
    UNION ALL
    SELECT
        s.INNERCODE AS innercode,
        s.SECUCODE AS instrument_code,
        s.SECUABBR AS instrument_name
    FROM secumain s
    WHERE s.SECUCATEGORY = 4
      AND s.SECUCODE = '000300'
),
quote_data AS (
    SELECT
        'quote' AS data_type,
        ti.instrument_code,
        ti.instrument_name,
        q.TRADINGDAY AS data_date,
        CAST(NULL AS DATETIME) AS info_publish_date,
        CAST(NULL AS DECIMAL(19, 8)) AS data_value,
        q.CLOSEPRICE AS close_price,
        q.CHANGEPCT / 100.0 AS daily_return
    FROM target_indices ti
    JOIN qt_indexquote q
      ON q.INNERCODE = ti.innercode
    JOIN trade_days td
      ON td.TRADINGDATE = q.TRADINGDAY
    WHERE q.CLOSEPRICE IS NOT NULL
      AND q.CLOSEPRICE > 0
      AND q.CHANGEPCT IS NOT NULL
)
SELECT *
FROM pmi_data
UNION ALL
SELECT *
FROM calendar_data
UNION ALL
SELECT *
FROM quote_data
ORDER BY data_type, instrument_code, data_date;
