WITH params AS (
    SELECT DATE('2024-12-31') AS as_of_date
),
obs_day AS (
    SELECT MAX(q.TRADINGDAY) AS obs_date
    FROM bond_conbdexchangequote q
    JOIN params p
      ON q.TRADINGDAY <= p.as_of_date
),
latest_quote AS (
    SELECT
        q.INNERCODE,
        q.TRADINGDAY,
        q.CLOSEPRICE,
        q.CONVERTPREMIUMRATE,
        q.YRMAT,
        ROW_NUMBER() OVER (
            PARTITION BY q.INNERCODE
            ORDER BY q.UPDATETIME DESC, q.ID DESC
        ) AS rn
    FROM bond_conbdexchangequote q FORCE INDEX (IX_BOND_CONBDEXCHANGEQUOTE)
    JOIN obs_day o
      ON q.TRADINGDAY = o.obs_date
    WHERE q.CLOSEPRICE IS NOT NULL
      AND q.CLOSEPRICE > 0
      AND q.CONVERTPREMIUMRATE IS NOT NULL
      AND q.BONDNATURE = 1
),
active_universe AS (
    SELECT
        b.INNERCODE,
        bc.SECUCODE,
        bc.SECUABBR,
        lq.CLOSEPRICE AS close_price,
        lq.CONVERTPREMIUMRATE AS convert_premium_rate,
        COALESCE(lq.YRMAT, DATEDIFF(DATE(b.ENDDATE), DATE(o.obs_date)) / 365.25) AS remaining_maturity_years,
        b.STARTDATE AS start_date,
        b.ENDDATE AS end_date,
        b.MATURITY,
        b.PARVALUE,
        b.COUPONRATE,
        b.LATESTCOUPONRATE,
        b.CALLPRICEEND,
        b.REDEEMOFINTEREST,
        b.INTERESTITEM,
        o.obs_date
    FROM latest_quote lq
    JOIN bond_conbdbasicinfo b
      ON lq.INNERCODE = b.INNERCODE
    JOIN bond_code bc
      ON lq.INNERCODE = bc.INNERCODE
    CROSS JOIN obs_day o
    WHERE lq.rn = 1
      AND b.BONDFORM = 1
      AND bc.BONDTYPELEVEL1DESC = '可转债'
      AND bc.BONDTYPELEVEL2DESC = '可转债'
      AND lq.CLOSEPRICE < 110
      AND COALESCE(lq.YRMAT, DATEDIFF(DATE(b.ENDDATE), DATE(o.obs_date)) / 365.25) >= 1
      AND (b.LISTEDDATE IS NULL OR DATE(b.LISTEDDATE) <= DATE(o.obs_date))
      AND (b.ENDDATE IS NULL OR DATE(b.ENDDATE) >= DATE(o.obs_date))
      AND (b.LISTEDENDDATE IS NULL OR DATE(b.LISTEDENDDATE) >= DATE(o.obs_date))
      AND (b.TRADEENDDATE IS NULL OR DATE(b.TRADEENDDATE) >= DATE(o.obs_date))
      AND (b.LASTTRADINGDATE IS NULL OR DATE(b.LASTTRADINGDATE) >= DATE(o.obs_date))
      AND (b.DELISTDATE IS NULL OR DATE(b.DELISTDATE) >= DATE(o.obs_date))
      AND (bc.LISTEDDATE IS NULL OR DATE(bc.LISTEDDATE) <= DATE(o.obs_date))
      AND (bc.INTERESTENDDATE IS NULL OR DATE(bc.INTERESTENDDATE) >= DATE(o.obs_date))
      AND (bc.DELISTDATE IS NULL OR DATE(bc.DELISTDATE) >= DATE(o.obs_date))
      AND bc.SECUABBR NOT LIKE '%退%'
      AND NOT EXISTS (
          SELECT 1
          FROM bond_conbdcallinfo c
          WHERE c.INNERCODE = b.INNERCODE
            AND c.CALLENDDATE IS NOT NULL
            AND DATE(c.CALLENDDATE) > DATE(o.obs_date)
            AND DATE(c.CALLENDDATE) <= DATE_ADD(DATE(o.obs_date), INTERVAL 60 DAY)
            AND (c.INSERTTIME IS NULL OR DATE(c.INSERTTIME) <= DATE(o.obs_date))
      )
)
SELECT *
FROM active_universe
ORDER BY SECUCODE;
