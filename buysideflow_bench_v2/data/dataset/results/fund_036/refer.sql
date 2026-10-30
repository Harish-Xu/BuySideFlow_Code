WITH params AS (
    SELECT
        DATE('2025-07-11') AS as_of_date,
        DATE_ADD(DATE('2025-07-11'), INTERVAL 1 DAY) AS visible_cutoff
),
target_fund AS (
    SELECT
        fa.INNERCODE,
        fa.SECURITYCODE AS fund_code,
        sm.SECUABBR AS fund_name,
        DATE(fa.ESTABLISHMENTDATE) AS start_date
    FROM mf_fundarchives fa
    JOIN secumain sm
        ON sm.INNERCODE = fa.INNERCODE
       AND sm.SECUCATEGORY = 8
    JOIN params p ON 1 = 1
    WHERE fa.SECURITYCODE = '017515'
      AND fa.MAINCODE = fa.SECURITYCODE
      AND fa.ESTABLISHMENTDATE <= p.as_of_date
      AND (fa.EXPIREDATE IS NULL OR fa.EXPIREDATE >= p.as_of_date)
      AND (fa.INSERTTIME IS NULL OR fa.INSERTTIME < p.visible_cutoff)
    ORDER BY fa.INNERCODE
    LIMIT 1
),
target_index AS (
    SELECT
        sm.INNERCODE,
        sm.SECUCODE,
        sm.SECUABBR
    FROM lc_indexbasicinfo ibi
    JOIN secumain sm
        ON sm.INNERCODE = ibi.INDEXCODE
    JOIN params p ON 1 = 1
    WHERE sm.SECUCODE = '899050'
      AND sm.SECUABBR = '北证50'
      AND ibi.INDEXPRICETYPE = 1
      AND ibi.INDEXSERIES = 101600
      AND ibi.PUBDATE <= p.as_of_date
    ORDER BY sm.INNERCODE
    LIMIT 1
),
fund_nav_latest AS (
    SELECT
        fn.TRADINGDAY,
        fn.UNITNVRESTORED AS price_value,
        ROW_NUMBER() OVER (
            PARTITION BY fn.TRADINGDAY
            ORDER BY fn.INSERTTIME DESC, fn.UPDATETIME DESC, fn.ID DESC
        ) AS rn
    FROM mf_fundnetvalueretrans fn
    JOIN target_fund tf
        ON tf.INNERCODE = fn.INNERCODE
    JOIN params p ON 1 = 1
    WHERE fn.TRADINGDAY BETWEEN tf.start_date AND p.as_of_date
      AND fn.UNITNVRESTORED IS NOT NULL
      AND fn.INSERTTIME < p.visible_cutoff
),
index_quote_latest AS (
    SELECT
        q.TRADINGDAY,
        q.CLOSEPRICE AS price_value,
        ROW_NUMBER() OVER (
            PARTITION BY q.TRADINGDAY
            ORDER BY q.XGRQ DESC, q.ID DESC
        ) AS rn
    FROM qt_indexquote q
    JOIN target_index ti
        ON ti.INNERCODE = q.INNERCODE
    JOIN target_fund tf ON 1 = 1
    JOIN params p ON 1 = 1
    WHERE q.TRADINGDAY BETWEEN tf.start_date AND p.as_of_date
      AND q.CLOSEPRICE IS NOT NULL
),
series AS (
    SELECT
        'fund' AS asset_type,
        TRADINGDAY,
        price_value
    FROM fund_nav_latest
    WHERE rn = 1

    UNION ALL

    SELECT
        'index' AS asset_type,
        TRADINGDAY,
        price_value
    FROM index_quote_latest
    WHERE rn = 1
),
running_peak AS (
    SELECT
        s.asset_type,
        s.TRADINGDAY,
        s.price_value,
        MAX(s.price_value) OVER (
            PARTITION BY s.asset_type
            ORDER BY s.TRADINGDAY
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS peak_value
    FROM series s
),
drawdown_detail AS (
    SELECT
        rp.asset_type,
        rp.TRADINGDAY,
        rp.price_value,
        rp.peak_value,
        MAX(CASE WHEN rp.price_value = rp.peak_value THEN rp.TRADINGDAY END) OVER (
            PARTITION BY rp.asset_type
            ORDER BY rp.TRADINGDAY
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS peak_date,
        rp.price_value / NULLIF(rp.peak_value, 0) - 1.0 AS drawdown
    FROM running_peak rp
),
max_drawdown AS (
    SELECT *
    FROM (
        SELECT
            dd.*,
            ROW_NUMBER() OVER (
                PARTITION BY dd.asset_type
                ORDER BY dd.drawdown ASC, dd.TRADINGDAY ASC
            ) AS rn
        FROM drawdown_detail dd
    ) x
    WHERE rn = 1
),
fund_recovery AS (
    SELECT
        MIN(s.TRADINGDAY) AS recovery_date
    FROM series s
    JOIN max_drawdown md
        ON md.asset_type = 'fund'
       AND s.asset_type = 'fund'
    WHERE s.TRADINGDAY > md.TRADINGDAY
      AND s.price_value >= md.peak_value
)
SELECT
    DATE_FORMAT(fmd.peak_date, '%Y-%m-%d') AS `基金峰值日期`,
    DATE_FORMAT(fmd.TRADINGDAY, '%Y-%m-%d') AS `基金谷底日期`,
    ROUND(fmd.drawdown * 100, 2) AS `基金最大回撤(%)`,
    CASE WHEN fr.recovery_date IS NULL THEN '否' ELSE '是' END AS `是否已修复`,
    CASE
        WHEN fr.recovery_date IS NULL THEN NULL
        ELSE DATEDIFF(fr.recovery_date, fmd.TRADINGDAY)
    END AS `修复天数`,
    ROUND(imd.drawdown * 100, 2) AS `北证50指数同期最大回撤(%)`
FROM max_drawdown fmd
JOIN max_drawdown imd
    ON imd.asset_type = 'index'
LEFT JOIN fund_recovery fr
    ON 1 = 1
WHERE fmd.asset_type = 'fund';
