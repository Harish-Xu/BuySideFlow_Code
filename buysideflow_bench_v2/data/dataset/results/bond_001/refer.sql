-- Same ChinaBond government yield series, bounded to the complete 20-month
-- baseline plus its preceding month for the first daily change. Macro values
-- are percentages; normalize to the decimal units of bond_cbyieldcurve.YIELD.
WITH daily_ranked AS (
    SELECT
        DATE(m.ENDDATE) AS curve_date,
        m.INDICATORCODE,
        m.DATAVALUE / 100.0 AS yield_value,
        ROW_NUMBER() OVER (
            PARTITION BY m.INDICATORCODE, DATE(m.ENDDATE)
            ORDER BY m.INFOPUBLDATE DESC, m.JSID DESC
        ) AS rn
    FROM c_ed_macroindicatordata m FORCE INDEX (IX_C_ED_MID)
    WHERE m.INDICATORCODE IN (
        600020006, -- 中债国债到期收益率:2年:日
        600020013  -- 中债国债到期收益率:10年:日
    )
      AND m.ENDDATE >= DATE('{{fetch_start_date}}')
      AND m.ENDDATE < DATE_ADD(DATE('{{end_date}}'), INTERVAL 1 DAY)
      AND m.INFOPUBLDATE < DATE_ADD(DATE('{{end_date}}'), INTERVAL 1 DAY)
)
SELECT
    curve_date,
    MAX(CASE WHEN INDICATORCODE = 600020013 THEN yield_value END) AS yield_10y,
    MAX(CASE WHEN INDICATORCODE = 600020006 THEN yield_value END) AS yield_2y
FROM daily_ranked
WHERE rn = 1
GROUP BY curve_date
HAVING yield_10y IS NOT NULL
   AND yield_2y IS NOT NULL
ORDER BY curve_date;
