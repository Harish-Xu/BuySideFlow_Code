-- name: pmi_values
WITH ranked AS (
    SELECT
        DATE(m.ENDDATE) AS month_end,
        DATE(m.INFOPUBLDATE) AS publish_date,
        m.DATAVALUE AS pmi_value,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(m.ENDDATE)
            ORDER BY
                m.INFOPUBLDATE DESC,
                m.UPDATETIME DESC,
                m.JSID DESC,
                m.ID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    JOIN c_ed_indicatormain i
      ON i.INDICATORCODE = m.INDICATORCODE
    WHERE m.INDICATORCODE = $pmi_code
      AND i.DISCLOSUREFREQUENCY = 30
      AND m.ENDDATE >= DATE_SUB(DATE('$start_date'), INTERVAL 2 MONTH)
      AND m.INFOPUBLDATE < DATE_ADD(DATE('$observe_date'), INTERVAL 1 DAY)
      AND m.DATAVALUE IS NOT NULL
      AND m.INFOPUBLDATE IS NOT NULL
)
SELECT
    month_end,
    publish_date,
    pmi_value
FROM ranked
WHERE rn = 1
ORDER BY month_end

-- name: gov10_curve
-- Same ChinaBond government 10Y maturity yield, including pre-2023 history.
-- The macro indicator stores percent values; calculations below use decimals.
WITH ranked AS (
    SELECT
        DATE(m.ENDDATE) AS curve_date,
        m.DATAVALUE / 100.0 AS yield_10y,
        ROW_NUMBER() OVER (
            PARTITION BY DATE(m.ENDDATE)
            ORDER BY
                m.INFOPUBLDATE DESC,
                m.UPDATETIME DESC,
                m.JSID DESC,
                m.ID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m
    WHERE m.INDICATORCODE = $indicator_code
      AND m.DATAVALUE IS NOT NULL
      AND m.ENDDATE BETWEEN DATE('$curve_start_date') AND DATE('$curve_end_date')
      AND m.INFOPUBLDATE < DATE_ADD(DATE('$curve_end_date'), INTERVAL 1 DAY)
)
SELECT
    curve_date,
    yield_10y
FROM ranked
WHERE rn = 1
ORDER BY curve_date
