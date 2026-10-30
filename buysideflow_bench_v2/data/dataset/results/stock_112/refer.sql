WITH
params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
index_info AS (
    SELECT
        INNERCODE,
        SECUCODE,
        COALESCE(SECUABBR, CHINAMEABBR, CHINAME, SECUCODE) AS index_name,
        CASE
            WHEN SECUCODE = '000016' THEN 'large_cap'
            WHEN SECUCODE = '399303' THEN 'small_cap'
        END AS style_bucket
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE IN ('000016', '399303')
),
quote_base AS (
    SELECT
        ii.INNERCODE,
        ii.SECUCODE,
        ii.index_name,
        ii.style_bucket,
        qi.TRADINGDAY,
        qi.CLOSEPRICE,
        qi.TURNOVERVALUE,
        LAG(qi.CLOSEPRICE) OVER (
            PARTITION BY ii.INNERCODE
            ORDER BY qi.TRADINGDAY
        ) AS prev_close,
        ROW_NUMBER() OVER (
            PARTITION BY ii.INNERCODE
            ORDER BY qi.TRADINGDAY DESC
        ) AS rn_desc
    FROM index_info ii
    JOIN qt_indexquote qi
      ON qi.INNERCODE = ii.INNERCODE
    JOIN params p
      ON qi.TRADINGDAY <= p.as_of_date
    WHERE qi.CLOSEPRICE > 0
),
metrics AS (
    SELECT
        SECUCODE,
        index_name,
        style_bucket,
        MAX(CASE WHEN rn_desc = 1 THEN TRADINGDAY END) AS latest_trade_day,
        MAX(CASE WHEN rn_desc = 1 THEN CLOSEPRICE END)
            / NULLIF(MAX(CASE WHEN rn_desc = 21 THEN CLOSEPRICE END), 0) - 1
            AS return_20d,
        AVG(CASE WHEN rn_desc BETWEEN 1 AND 5 THEN TURNOVERVALUE END)
            / NULLIF(AVG(CASE WHEN rn_desc BETWEEN 1 AND 60 THEN TURNOVERVALUE END), 0) - 1
            AS turnover_value_change_5d_60d,
        COUNT(CASE WHEN rn_desc IN (1, 21) THEN 1 END) AS return_price_count,
        COUNT(CASE WHEN rn_desc BETWEEN 1 AND 5 AND TURNOVERVALUE IS NOT NULL THEN 1 END) AS turnover_5d_count,
        COUNT(CASE WHEN rn_desc BETWEEN 1 AND 60 AND TURNOVERVALUE IS NOT NULL THEN 1 END) AS turnover_60d_count
    FROM quote_base
    WHERE rn_desc <= 61
    GROUP BY SECUCODE, index_name, style_bucket
),
valid_metrics AS (
    SELECT *
    FROM metrics
    WHERE return_price_count = 2
      AND turnover_5d_count = 5
      AND turnover_60d_count = 60
),
signal_input AS (
    SELECT
        MAX(latest_trade_day) AS calc_date,
        MAX(CASE WHEN style_bucket = 'large_cap' THEN return_20d END) AS large_ret,
        MAX(CASE WHEN style_bucket = 'small_cap' THEN return_20d END) AS small_ret,
        MAX(CASE WHEN style_bucket = 'large_cap' THEN turnover_value_change_5d_60d END) AS large_tv,
        MAX(CASE WHEN style_bucket = 'small_cap' THEN turnover_value_change_5d_60d END) AS small_tv
    FROM valid_metrics
)
SELECT
    DATE_FORMAT(calc_date, '%Y-%m-%d') AS 计算日,
    CASE
        WHEN large_ret > small_ret AND large_tv > small_tv THEN '大盘占优'
        WHEN small_ret > large_ret AND small_tv > large_tv THEN '小盘占优'
        ELSE '风格不明显'
    END AS 风格信号,
    ROUND(large_ret * 100, 2) AS `上证50近20日收益`,
    ROUND(small_ret * 100, 2) AS `国证2000近20日收益`,
    ROUND(large_tv * 100, 2) AS `上证50成交额变化`,
    ROUND(small_tv * 100, 2) AS `国证2000成交额变化`
FROM signal_input;
