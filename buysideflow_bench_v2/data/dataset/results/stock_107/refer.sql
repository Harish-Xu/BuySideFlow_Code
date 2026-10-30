-- name: trading_days
SELECT TRADINGDATE AS trade_date
FROM qt_tradingdaynew
WHERE SECUMARKET = 83
  AND IFTRADINGDAY = 1
  AND TRADINGDATE BETWEEN '$start_date' AND '$end_date'
ORDER BY TRADINGDATE;

-- name: factor_panel
WITH
schedule AS (
$schedule_rows
),
target_index AS (
    SELECT INNERCODE
    FROM secumain
    WHERE SECUCATEGORY = 4
      AND SECUCODE = '$index_code'
    ORDER BY INNERCODE
    LIMIT 1
),
active_components AS (
    SELECT DISTINCT
        s.cutoff_date,
        s.obs_date,
        s.forward_date,
        s.report_date,
        s.prior_same_date,
        s.prior_annual_date,
        sm.INNERCODE,
        sm.COMPANYCODE
    FROM schedule s
    CROSS JOIN target_index idx
    JOIN lc_indexcomponent ic
      ON ic.INDEXINNERCODE = idx.INNERCODE
     AND ic.INDATE < DATE_ADD(s.obs_date, INTERVAL 1 DAY)
     AND (ic.OUTDATE IS NULL OR ic.OUTDATE > s.obs_date)
    JOIN secumain sm
      ON sm.INNERCODE = ic.SECUINNERCODE
     AND sm.SECUCATEGORY = 1
     AND sm.COMPANYCODE IS NOT NULL
),
financial_ranked AS (
    SELECT
        ac.obs_date,
        ac.COMPANYCODE,
        f.ENDDATE,
        f.NPFROMPARENTCOMPANYOWNERS,
        f.NETOPERATECASHFLOW,
        f.TOTALASSETS,
        ROW_NUMBER() OVER (
            PARTITION BY ac.obs_date, ac.COMPANYCODE, f.ENDDATE
            ORDER BY
                CASE f.MARK
                    WHEN 1 THEN 1
                    WHEN 8 THEN 2
                    WHEN 7 THEN 3
                    WHEN 6 THEN 4
                    WHEN 2 THEN 5
                    ELSE 9
                END,
                f.INFOPUBLDATE DESC,
                f.INSERTTIME DESC,
                f.JSID DESC,
                f.ID DESC
        ) AS rn
    FROM active_components ac
    JOIN lc_maindatanew f
     ON f.COMPANYCODE = ac.COMPANYCODE
     AND f.INFOPUBLDATE < DATE_ADD(ac.obs_date, INTERVAL 1 DAY)
     AND f.ENDDATE IN (ac.report_date, ac.prior_same_date, ac.prior_annual_date)
    WHERE COALESCE(f.ACCOUNTINGSTANDARDS, 1) = 1
      AND f.MARK IN (1, 8, 7, 6, 2)
),
financial_panel AS (
    SELECT
        ac.obs_date,
        ac.INNERCODE,
        ac.COMPANYCODE,
        ac.forward_date,
        MAX(CASE WHEN fr.ENDDATE = ac.report_date THEN fr.NPFROMPARENTCOMPANYOWNERS END) AS current_np,
        MAX(CASE WHEN fr.ENDDATE = ac.report_date THEN fr.NETOPERATECASHFLOW END) AS current_cfo,
        MAX(CASE WHEN fr.ENDDATE = ac.report_date THEN fr.TOTALASSETS END) AS current_assets,
        MAX(CASE WHEN fr.ENDDATE = ac.prior_same_date THEN fr.NPFROMPARENTCOMPANYOWNERS END) AS prior_same_np,
        MAX(CASE WHEN fr.ENDDATE = ac.prior_same_date THEN fr.NETOPERATECASHFLOW END) AS prior_same_cfo,
        MAX(CASE WHEN fr.ENDDATE = ac.prior_same_date THEN fr.TOTALASSETS END) AS prior_same_assets,
        MAX(CASE WHEN fr.ENDDATE = ac.prior_annual_date THEN fr.NPFROMPARENTCOMPANYOWNERS END) AS prior_annual_np,
        MAX(CASE WHEN fr.ENDDATE = ac.prior_annual_date THEN fr.NETOPERATECASHFLOW END) AS prior_annual_cfo
    FROM active_components ac
    LEFT JOIN financial_ranked fr
      ON fr.obs_date = ac.obs_date
     AND fr.COMPANYCODE = ac.COMPANYCODE
     AND fr.rn = 1
    GROUP BY ac.obs_date, ac.INNERCODE, ac.COMPANYCODE, ac.forward_date
),
financial_industries AS (
    SELECT DISTINCT ac.obs_date, ac.COMPANYCODE
    FROM active_components ac
    JOIN lc_exgindustry e
     ON e.COMPANYCODE = ac.COMPANYCODE
     AND e.STANDARD = 38
     AND e.INFOPUBLDATE < DATE_ADD(ac.obs_date, INTERVAL 1 DAY)
     AND (e.CANCELDATE IS NULL OR e.CANCELDATE > ac.obs_date)
    WHERE e.FIRSTINDUSTRYNAME IN ('银行', '非银金融', '金融服务')
),
factor_values AS (
    SELECT
        fp.obs_date,
        fp.INNERCODE,
        fp.COMPANYCODE,
        fp.forward_date,
        -1 * (
            (fp.current_np - fp.prior_same_np + fp.prior_annual_np)
            - (fp.current_cfo - fp.prior_same_cfo + fp.prior_annual_cfo)
        ) / NULLIF((fp.current_assets + fp.prior_same_assets) / 2, 0)
            AS low_accrual_score
    FROM financial_panel fp
    LEFT JOIN financial_industries fi
      ON fi.obs_date = fp.obs_date
     AND fi.COMPANYCODE = fp.COMPANYCODE
    WHERE fi.COMPANYCODE IS NULL
      AND fp.current_np IS NOT NULL
      AND fp.current_cfo IS NOT NULL
      AND fp.current_assets IS NOT NULL
      AND fp.prior_same_np IS NOT NULL
      AND fp.prior_same_cfo IS NOT NULL
      AND fp.prior_same_assets IS NOT NULL
      AND fp.prior_annual_np IS NOT NULL
      AND fp.prior_annual_cfo IS NOT NULL
      AND (fp.current_assets + fp.prior_same_assets) / 2 > 0
)
SELECT
    fv.obs_date,
    fv.INNERCODE,
    fv.low_accrual_score,
    p1.BACKWARDPRICE / NULLIF(p0.BACKWARDPRICE, 0) - 1 AS forward_return
FROM factor_values fv
JOIN qt_performancedata p0
  ON p0.TRADINGDAY = fv.obs_date
 AND p0.INNERCODE = fv.INNERCODE
JOIN qt_performancedata p1
  ON p1.TRADINGDAY = fv.forward_date
 AND p1.INNERCODE = fv.INNERCODE
WHERE p0.BACKWARDPRICE > 0
  AND p1.BACKWARDPRICE > 0
  AND NOT EXISTS (
      SELECT 1
      FROM cs_riskalert ra
      WHERE ra.INNERCODE = fv.INNERCODE
        AND ra.RISKALERTTYPE IN (1, 5, 12, 14)
        AND ra.IMPLEMENTDATE < DATE_ADD(fv.obs_date, INTERVAL 1 DAY)
        AND COALESCE(ra.IMPLANNOUCEDATE, ra.INSERTTIME, ra.IMPLEMENTDATE) < DATE_ADD(fv.obs_date, INTERVAL 1 DAY)
        AND (
            ra.REMOVEDATE IS NULL
            OR ra.REMOVEDATE > fv.obs_date
            OR COALESCE(ra.REMOVEINFOPUBLDATE, ra.INSERTTIME, ra.REMOVEDATE) >= DATE_ADD(fv.obs_date, INTERVAL 1 DAY)
        )
  )
ORDER BY fv.obs_date, fv.INNERCODE;
