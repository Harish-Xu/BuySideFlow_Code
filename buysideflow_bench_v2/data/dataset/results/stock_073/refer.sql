WITH params AS (
    SELECT DATE('2025-12-31') AS as_of_date
),
trade_asof AS (
    SELECT MAX(f.TRADINGDATE) AS trade_date
    FROM qt_tradingdaynew f
    JOIN params p
      ON f.TRADINGDATE <= p.as_of_date
    WHERE f.SECUMARKET = 83 AND f.IFTRADINGDAY = 1
),
window_start AS (
    SELECT MIN(TRADINGDATE) AS start_date FROM (
        SELECT DISTINCT f.TRADINGDATE FROM qt_tradingdaynew f
        JOIN trade_asof a ON f.TRADINGDATE <= a.trade_date
        WHERE f.SECUMARKET = 83 AND f.IFTRADINGDAY = 1
        ORDER BY f.TRADINGDATE DESC LIMIT 25
    ) dates
),
etf_codes AS (
    SELECT
        s.INNERCODE,
        s.SECUCODE,
        COALESCE(s.SECUABBR, s.CHINAMEABBR, s.CHINAME, s.SECUCODE) AS etf_name
    FROM secumain s
    WHERE s.SECUCODE IN ('518880', '513100', '159915', '510180')
      AND s.SECUCATEGORY = 8
),
recent_prices AS (
    SELECT
        t.INNERCODE,
        t.TRADINGDAY,
        t.CLOSEPRICE
    FROM (
        SELECT
            f.INNERCODE,
            f.TRADINGDAY,
            f.CLOSEPRICE,
            ROW_NUMBER() OVER (
                PARTITION BY f.INNERCODE
                ORDER BY f.TRADINGDAY DESC
            ) AS rn
        FROM qt_fundsperformancehis f
        JOIN etf_codes e
          ON e.INNERCODE = f.INNERCODE
        JOIN trade_asof ta
          ON f.TRADINGDAY <= ta.trade_date
        JOIN window_start ws ON f.TRADINGDAY >= ws.start_date
        WHERE f.CLOSEPRICE IS NOT NULL
          AND f.CLOSEPRICE > 0
    ) t
    WHERE t.rn <= 25
),
ordered_prices AS (
    SELECT
        rp.INNERCODE,
        rp.TRADINGDAY,
        rp.CLOSEPRICE,
        ROW_NUMBER() OVER (
            PARTITION BY rp.INNERCODE
            ORDER BY rp.TRADINGDAY ASC
        ) - 1 AS time_index
    FROM recent_prices rp
),
regression_stats AS (
    SELECT
        op.INNERCODE,
        COUNT(*) AS n,
        SUM(op.time_index) AS sum_x,
        SUM(LN(op.CLOSEPRICE)) AS sum_y,
        SUM(op.time_index * op.time_index) AS sum_x2,
        SUM(LN(op.CLOSEPRICE) * LN(op.CLOSEPRICE)) AS sum_y2,
        SUM(op.time_index * LN(op.CLOSEPRICE)) AS sum_xy
    FROM ordered_prices op
    GROUP BY op.INNERCODE
    HAVING COUNT(*) = 25
),
scored AS (
    SELECT
        e.SECUCODE AS etf_code,
        ta.trade_date AS calculation_date,
        (
            rs.n * rs.sum_xy - rs.sum_x * rs.sum_y
        ) / NULLIF(
            rs.n * rs.sum_x2 - rs.sum_x * rs.sum_x,
            0
        ) AS slope,
        POWER(
            (
                rs.n * rs.sum_xy - rs.sum_x * rs.sum_y
            ) / NULLIF(
                SQRT(
                    (rs.n * rs.sum_x2 - rs.sum_x * rs.sum_x) *
                    (rs.n * rs.sum_y2 - rs.sum_y * rs.sum_y)
                ),
                0
            ),
            2
        ) AS r_squared
    FROM regression_stats rs
    JOIN etf_codes e
      ON e.INNERCODE = rs.INNERCODE
    CROSS JOIN trade_asof ta
)
SELECT
    calculation_date,
    etf_code,
    ROUND(POWER(EXP(slope), 250) - 1, 6) AS annualized_return,
    ROUND(GREATEST(0, LEAST(1, r_squared)), 6) AS r_squared,
    ROUND((POWER(EXP(slope), 250) - 1) * GREATEST(0, LEAST(1, r_squared)), 6) AS composite_score
FROM scored
ORDER BY composite_score DESC, etf_code;
