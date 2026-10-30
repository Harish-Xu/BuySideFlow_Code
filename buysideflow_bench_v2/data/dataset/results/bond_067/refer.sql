WITH prev_dates AS (
    SELECT trading_day
    FROM (
        SELECT
            d.TRADINGDAY AS trading_day,
            ROW_NUMBER() OVER (ORDER BY d.TRADINGDAY DESC) AS rn
        FROM (
            SELECT DISTINCT TRADINGDAY
            FROM bond_conbdexchangequote
            WHERE TRADINGDAY <= '2024-12-30'
        ) d
    ) x
    WHERE rn <= 20
),
future_dates AS (
    SELECT trading_day
    FROM (
        SELECT
            d.TRADINGDAY AS trading_day,
            ROW_NUMBER() OVER (ORDER BY d.TRADINGDAY ASC) AS rn
        FROM (
            SELECT DISTINCT TRADINGDAY
            FROM bond_conbdexchangequote
            WHERE TRADINGDAY > '2024-12-30'
        ) d
    ) x
    WHERE rn <= 20
),
horizon_dates AS (
    SELECT DATE('2024-12-30') AS trading_day
    UNION ALL
    SELECT trading_day
    FROM future_dates
),
obs_universe AS (
    SELECT
        q.INNERCODE,
        bc.SECUCODE,
        bc.SECUABBR,
        q.CLOSEPRICE AS price,
        q.CONVERTPREMIUMRATE AS convert_premium_rate,
        PERCENT_RANK() OVER (ORDER BY q.CONVERTPREMIUMRATE) AS premium_percentile,
        PERCENT_RANK() OVER (ORDER BY q.CONVERTPREMIUMRATE DESC) AS premium_top_rank
    FROM bond_conbdexchangequote q
    JOIN bond_conbdbasicinfo b
      ON b.INNERCODE = q.INNERCODE
    JOIN bond_code bc
      ON bc.INNERCODE = q.INNERCODE
    WHERE q.TRADINGDAY = '2024-12-30'
      AND q.CLOSEPRICE IS NOT NULL
      AND q.CLOSEPRICE > 0
      AND q.CONVERTPREMIUMRATE IS NOT NULL
      AND q.BONDNATURE = 1
      AND q.TURNOVERVALUE > 0
      AND bc.BONDTYPELEVEL1 = 2200
      AND (b.LISTEDDATE IS NULL OR DATE(b.LISTEDDATE) <= '2024-12-30')
      AND (b.ENDDATE IS NULL OR DATE(b.ENDDATE) >= '2024-12-30')
      AND (b.LISTEDENDDATE IS NULL OR DATE(b.LISTEDENDDATE) >= '2024-12-30')
      AND (b.TRADEENDDATE IS NULL OR DATE(b.TRADEENDDATE) >= '2024-12-30')
      AND (b.LASTTRADINGDATE IS NULL OR DATE(b.LASTTRADINGDATE) >= '2024-12-30')
      AND (b.DELISTDATE IS NULL OR DATE(b.DELISTDATE) >= '2024-12-30')
      AND (bc.LISTEDDATE IS NULL OR DATE(bc.LISTEDDATE) <= '2024-12-30')
      AND (bc.INTERESTENDDATE IS NULL OR DATE(bc.INTERESTENDDATE) >= '2024-12-30')
      AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) >= '2024-12-30')
),
turnover_daily AS (
    SELECT
        u.INNERCODE,
        d.trading_day,
        MAX(q.TURNOVERVALUE) AS TURNOVERVALUE
    FROM obs_universe u
    CROSS JOIN prev_dates d
    JOIN bond_conbdexchangequote q
      ON q.INNERCODE = u.INNERCODE
     AND q.TRADINGDAY = d.trading_day
     AND q.TURNOVERVALUE > 0
    GROUP BY u.INNERCODE, d.trading_day
),
turnover_ranked AS (
    SELECT
        INNERCODE,
        TURNOVERVALUE,
        ROW_NUMBER() OVER (
            PARTITION BY INNERCODE
            ORDER BY TURNOVERVALUE
        ) AS rn,
        COUNT(*) OVER (PARTITION BY INNERCODE) AS cnt
    FROM turnover_daily
),
turnover_median AS (
    SELECT
        INNERCODE,
        AVG(TURNOVERVALUE) AS turnover_value_median_20d
    FROM turnover_ranked
    WHERE rn IN ((cnt + 1) DIV 2, (cnt + 2) DIV 2)
    GROUP BY INNERCODE
),
market_turnover_ranked AS (
    SELECT
        turnover_value_median_20d,
        ROW_NUMBER() OVER (ORDER BY turnover_value_median_20d) AS rn,
        COUNT(*) OVER () AS cnt
    FROM turnover_median
),
market_turnover_median AS (
    SELECT AVG(turnover_value_median_20d) AS market_turnover_value_median_20d
    FROM market_turnover_ranked
    WHERE rn IN ((cnt + 1) DIV 2, (cnt + 2) DIV 2)
),
candidate AS (
    SELECT
        u.*,
        t.turnover_value_median_20d
    FROM obs_universe u
    JOIN turnover_median t
      ON t.INNERCODE = u.INNERCODE
    CROSS JOIN market_turnover_median m
    WHERE u.price > 120
      AND u.premium_top_rank <= 0.1
      AND t.turnover_value_median_20d >= m.market_turnover_value_median_20d
),
ranked_candidate AS (
    SELECT
        c.*,
        ROW_NUMBER() OVER (
            ORDER BY c.premium_percentile DESC, c.price DESC, c.SECUCODE
        ) AS output_rank
    FROM candidate c
),
drawdown_base AS (
    SELECT
        c.INNERCODE,
        q.TRADINGDAY,
        q.CLOSEPRICE,
        MAX(q.CLOSEPRICE) OVER (
            PARTITION BY c.INNERCODE
            ORDER BY q.TRADINGDAY
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS running_peak
    FROM ranked_candidate c
    JOIN bond_conbdexchangequote q
      ON q.INNERCODE = c.INNERCODE
    JOIN horizon_dates d
      ON q.TRADINGDAY = d.trading_day
    WHERE c.output_rank <= 20
      AND q.CLOSEPRICE IS NOT NULL
      AND q.CLOSEPRICE > 0
),
future_drawdown AS (
    SELECT
        INNERCODE,
        COUNT(*) AS path_days,
        MIN(CLOSEPRICE / NULLIF(running_peak, 0) - 1.0) AS future_20d_max_drawdown
    FROM drawdown_base
    GROUP BY INNERCODE
    HAVING path_days = 21
)
SELECT
    c.SECUCODE AS `证券代码`,
    c.SECUABBR AS `转债简称`,
    ROUND(c.price, 2) AS `收盘价`,
    ROUND(c.convert_premium_rate, 2) AS `转股溢价率`,
    ROUND(c.premium_percentile * 100, 2) AS `溢价率分位`,
    ROUND(c.turnover_value_median_20d, 2) AS `近20日成交额中位数`,
    ROUND(d.future_20d_max_drawdown * 100, 2) AS `未来20日最大回撤`
FROM ranked_candidate c
JOIN future_drawdown d
  ON d.INNERCODE = c.INNERCODE
WHERE c.output_rank <= 20
ORDER BY c.output_rank;
